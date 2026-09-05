"""LlamaIndex adapter.

`QueryPipeline` is an explicit DAG API -- `add_modules` names the steps and
`add_link` names the edges -- which makes it directly extractable.

    p = QueryPipeline()
    p.add_modules({"input": InputComponent(), "retriever": retriever, "llm": llm})
    p.add_link("input", "retriever")
    p.add_link("retriever", "prompt", dest_key="context_str")

Entry and terminal points are derived: a module nothing links into is a start,
and one that links nowhere is an end. Pipelines assembled from a variable, and
the older chained query-engine style, are not extracted -- neither states its
structure in a form an AST walk can read.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..analyze.python import (Finding, FileReport, _chain, _const_str, _kwarg,
                              iter_python_files)
from .base import AdapterResult

__all__ = ["LlamaIndexAdapter"]

CONF_MODULE = 0.94
CONF_LINK = 0.95


class _Walker(ast.NodeVisitor):
    # Module classes whose role in the pipeline is unambiguous from their name.
    TYPES = {
        "InputComponent": "input", "retriever": "retriever",
        "as_retriever": "retriever", "PromptTemplate": "prompt",
        "ChatPromptTemplate": "prompt", "RichPromptTemplate": "prompt",
    }

    def __init__(self, path: str, module: str, retrievers: dict[str, str] | None = None):
        self.path, self.module = path, module
        self.findings: list[Finding] = []
        self.notes: list[str] = []
        self.pipelines: set[str] = set()
        # variable -> the store key it retrieves from, from the generic pass
        self.retrievers = retrievers or {}

    def add(self, kind, key, name, node, confidence, data):
        self.findings.append(Finding(
            kind=kind, key=key, name=name, file=self.path, line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno), symbol=self.module,
            confidence=confidence, data=data))

    def visit_Assign(self, node: ast.Assign):
        value = node.value
        if isinstance(value, ast.Call) and \
                _chain(value.func).rsplit(".", 1)[-1] == "QueryPipeline":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.pipelines.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        chain = _chain(node.func)
        if "." in chain:
            receiver, method = chain.rsplit(".", 1)
            if receiver.split(".")[-1] in self.pipelines:
                if method == "add_modules":
                    self._modules(receiver.split(".")[-1], node)
                elif method in ("add_link", "link"):
                    self._link(receiver.split(".")[-1], node)
        self.generic_visit(node)

    def _modules(self, pipeline: str, node: ast.Call):
        mapping = node.args[0] if node.args else _kwarg(node, "module_dict")
        if not isinstance(mapping, ast.Dict):
            self.notes.append(
                f"{self.path}:{node.lineno}: add_modules was given a non-literal "
                f"mapping; the pipeline's steps are missing from the graph")
            return
        for key, value in zip(mapping.keys, mapping.values):
            label = _const_str(key)
            if label is None:
                continue
            target = _chain(value.func if isinstance(value, ast.Call) else value)
            target = target.rsplit(".", 1)[-1] if target else None
            store = self.retrievers.get(target or "")
            node_type = self.TYPES.get(target or "")
            if node_type is None and store:
                node_type = "retriever"
            self.add("fw_node", f"fwnode:{pipeline}:{label}", label, node, CONF_MODULE,
                     {"builder": pipeline, "label": label, "target": target,
                      "node_type": node_type,
                      "llm": target, "prompts": [target] if target else [],
                      "stores": [store] if store else []})

    def _link(self, pipeline: str, node: ast.Call):
        args = list(node.args)
        source = _const_str(args[0]) if args else None
        target = _const_str(args[1]) if len(args) > 1 else None
        if source is None or target is None:
            self.notes.append(
                f"{self.path}:{node.lineno}: add_link with a non-literal endpoint; "
                f"the edge is missing from the graph")
            return
        dest_key = _const_str(_kwarg(node, "dest_key") or ast.Constant(None))
        self.add("fw_edge", f"fwedge:{pipeline}:{source}:{target}:{dest_key}",
                 f"{source}->{target}", node, CONF_LINK,
                 {"builder": pipeline, "source": source, "target": target,
                  "dest_key": dest_key})


class LlamaIndexAdapter:
    name = "aiflow-adapter-llamaindex"
    framework = "llamaindex"

    def detects(self, reports: list[FileReport]) -> bool:
        return any(self.framework in r.frameworks for r in reports)

    def analyze(self, root: Path, reports: list[FileReport]) -> AdapterResult:
        findings: list[Finding] = []
        notes: list[str] = []
        root = Path(root)

        # Which variable holds a retriever, and which store it reads -- known to
        # the generic pass, which saw `retriever = index.as_retriever(...)`.
        retrievers: dict[str, str] = {}
        for report in reports:
            for f in report.findings:
                if f.kind == "retrieval" and f.data.get("store"):
                    for var in f.data.get("assigned_to") or ():
                        retrievers[var] = f.data["store"]

        for path in iter_python_files(root):
            rel = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            walker = _Walker(rel, rel[:-3].replace("/", "."), retrievers)
            walker.visit(tree)
            findings.extend(walker.findings)
            notes.extend(walker.notes)

        modules = {f.data["label"]: f for f in findings if f.kind == "fw_node"}
        sources = {f.data["source"] for f in findings if f.kind == "fw_edge"}
        targets = {f.data["target"] for f in findings if f.kind == "fw_edge"}
        for label, f in modules.items():
            declared = f.data.get("node_type")
            # A module already typed `input` or `output` *is* the boundary; adding a
            # separate entry node beside it would give the graph two starts.
            if label not in targets and declared != "input":
                findings.append(Finding(
                    kind="fw_entry", key=f"fwentry:{label}", name=label, file=f.file,
                    line=f.line, symbol=f.symbol, confidence=0.85,
                    data={"builder": f.data["builder"], "label": label}))
            if label not in sources and declared != "output":
                findings.append(Finding(
                    kind="fw_terminal", key=f"fwterm:{label}", name=label, file=f.file,
                    line=f.line, symbol=f.symbol, confidence=0.85,
                    data={"builder": f.data["builder"], "label": label}))
        return AdapterResult(self.name, findings, notes=notes)
