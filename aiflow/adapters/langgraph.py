"""LangGraph adapter.

LangGraph states its topology explicitly -- `add_node`, `add_edge`,
`add_conditional_edges`, `START`, `END` -- which makes it the one place where
branching is genuinely extractable rather than guessed. This adapter is what
lets a generated document contain `condition` nodes and `routes_to` edges at
all; the generic analyzer deliberately refuses to invent them.

Recognised today:

    builder = StateGraph(State)
    builder.add_node("name", fn)              -> a step
    builder.add_edge("a", "b")                -> sequential flow
    builder.add_edge(START, "a")              -> entry
    builder.add_edge("z", END)                -> terminal
    builder.set_entry_point("a")              -> entry
    builder.set_finish_point("z")             -> terminal
    builder.add_conditional_edges(src, fn, {"label": "target"})  -> a branch

Not recognised: graphs whose nodes or routes are assembled from a variable at
runtime. Those are invisible to an AST walk, and the adapter reports what it
skipped rather than pretending the graph is complete.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..analyze.python import Finding, FileReport, _chain, _const_str, iter_python_files
from .base import AdapterResult

__all__ = ["LangGraphAdapter"]

BUILDER_CTORS = ("StateGraph", "MessageGraph", "Graph")
ENTRY_SENTINELS = {"START", "__start__"}
TERMINAL_SENTINELS = {"END", "__end__"}

CONF_NODE = 0.96
CONF_EDGE = 0.96
CONF_BRANCH = 0.94
CONF_DYNAMIC = 0.5


def _label(node: ast.AST) -> str | None:
    """Resolve a node label: a string literal, or a START/END sentinel."""
    literal = _const_str(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.Name) and node.id in ENTRY_SENTINELS | TERMINAL_SENTINELS:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in ENTRY_SENTINELS | TERMINAL_SENTINELS:
        return node.attr
    return None


def _is_entry(label: str) -> bool:
    return label in ENTRY_SENTINELS


def _is_terminal(label: str) -> bool:
    return label in TERMINAL_SENTINELS


def _callable_name(node: ast.AST) -> str | None:
    chain = _chain(node)
    return chain.rsplit(".", 1)[-1] if chain else None


class _Walker(ast.NodeVisitor):
    def __init__(self, path: str, module: str):
        self.path = path
        self.module = module
        self.builders: dict[str, str] = {}      # variable -> state type
        self.findings: list[Finding] = []
        self.notes: list[str] = []

    def add(self, kind: str, key: str, name: str, node: ast.AST, confidence: float,
            data: dict) -> None:
        self.findings.append(Finding(
            kind=kind, key=key, name=name, file=self.path, line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            symbol=f"{self.module}", confidence=confidence, data=data))

    # -- builder construction ---------------------------------------------
    def visit_Assign(self, node: ast.Assign):
        value = node.value
        if isinstance(value, ast.Call):
            ctor = _callable_name(value.func)
            if ctor in BUILDER_CTORS:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        state = None
                        if value.args:
                            state = _chain(value.args[0]) or None
                        self.builders[target.id] = state or ctor
                        self.add("fw_graph", f"graph:{self.path}:{target.id}",
                                 target.id, node, CONF_NODE,
                                 {"builder": target.id, "state": state, "ctor": ctor})
        self.generic_visit(node)

    # -- builder method calls ---------------------------------------------
    def visit_Call(self, node: ast.Call):
        chain = _chain(node.func)
        if "." in chain:
            receiver, method = chain.rsplit(".", 1)
            base = receiver.split(".")[-1]
            if base in self.builders:
                handler = getattr(self, f"_on_{method}", None)
                if handler:
                    handler(base, node)
        self.generic_visit(node)

    def _on_add_node(self, builder: str, node: ast.Call):
        args = list(node.args)
        label = _label(args[0]) if args else None
        target = None
        if len(args) > 1:
            target = _callable_name(args[1])
        elif args and not label:
            # add_node(fn) -- the function name becomes the label
            target = _callable_name(args[0])
            label = target
        if label is None:
            self.notes.append(
                f"{self.path}:{node.lineno}: add_node with a non-literal name; "
                f"the step is not in the graph")
            return
        self.add("fw_node", f"fwnode:{builder}:{label}", label, node, CONF_NODE,
                 {"builder": builder, "label": label, "target": target})

    def _on_add_edge(self, builder: str, node: ast.Call):
        if len(node.args) < 2:
            return
        source, target = _label(node.args[0]), _label(node.args[1])
        if source is None or target is None:
            self.notes.append(
                f"{self.path}:{node.lineno}: add_edge with a non-literal endpoint; "
                f"the edge is not in the graph")
            return
        if _is_entry(source):
            self.add("fw_entry", f"fwentry:{builder}:{target}", target, node,
                     CONF_EDGE, {"builder": builder, "label": target})
        elif _is_terminal(target):
            self.add("fw_terminal", f"fwterm:{builder}:{source}", source, node,
                     CONF_EDGE, {"builder": builder, "label": source})
        else:
            self.add("fw_edge", f"fwedge:{builder}:{source}:{target}",
                     f"{source}->{target}", node, CONF_EDGE,
                     {"builder": builder, "source": source, "target": target})

    def _on_set_entry_point(self, builder: str, node: ast.Call):
        label = _label(node.args[0]) if node.args else None
        if label:
            self.add("fw_entry", f"fwentry:{builder}:{label}", label, node,
                     CONF_EDGE, {"builder": builder, "label": label})

    def _on_set_finish_point(self, builder: str, node: ast.Call):
        label = _label(node.args[0]) if node.args else None
        if label:
            self.add("fw_terminal", f"fwterm:{builder}:{label}", label, node,
                     CONF_EDGE, {"builder": builder, "label": label})

    def _on_add_conditional_edges(self, builder: str, node: ast.Call):
        args = list(node.args)
        source = _label(args[0]) if args else None
        if source is None:
            self.notes.append(
                f"{self.path}:{node.lineno}: add_conditional_edges with a "
                f"non-literal source; the branch is not in the graph")
            return

        router = _callable_name(args[1]) if len(args) > 1 else None
        mapping_node = args[2] if len(args) > 2 else None
        for kw in node.keywords:
            if kw.arg in ("path_map", "conditional_edge_mapping"):
                mapping_node = kw.value
            elif kw.arg == "path":
                router = _callable_name(kw.value)

        mapping: dict[str, str] = {}
        dynamic = False
        if isinstance(mapping_node, ast.Dict):
            for key, value in zip(mapping_node.keys, mapping_node.values):
                k, v = _const_str(key), _label(value)
                if k is not None and v is not None:
                    mapping[k] = v
                else:
                    dynamic = True
        elif isinstance(mapping_node, (ast.List, ast.Tuple)):
            for element in mapping_node.elts:
                v = _label(element)
                if v is not None:
                    mapping[v] = v          # a list means label == destination
                else:
                    dynamic = True
        elif mapping_node is not None:
            dynamic = True

        if dynamic or (not mapping and mapping_node is not None):
            self.notes.append(
                f"{self.path}:{node.lineno}: branch targets are not all literal; "
                f"some routes are missing from the graph")

        self.add("fw_branch", f"fwbranch:{builder}:{source}",
                 router or f"{source}_branch", node,
                 CONF_BRANCH if mapping and not dynamic else CONF_DYNAMIC,
                 {"builder": builder, "source": source, "router": router,
                  "mapping": mapping, "partial": dynamic or not mapping})


class LangGraphAdapter:
    name = "aiflow-adapter-langgraph"
    framework = "langgraph"

    def detects(self, reports: list[FileReport]) -> bool:
        return any(self.framework in r.frameworks for r in reports)

    def analyze(self, root: Path, reports: list[FileReport]) -> AdapterResult:
        findings: list[Finding] = []
        notes: list[str] = []
        root = Path(root)
        for path in iter_python_files(root):
            rel = path.relative_to(root).as_posix()
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source, filename=rel)
            except SyntaxError:
                continue
            module = rel[:-3].replace("/", ".").removesuffix(".__init__")
            walker = _Walker(rel, module)
            walker.visit(tree)
            findings.extend(walker.findings)
            notes.extend(walker.notes)
        return AdapterResult(self.name, findings, notes=notes)
