"""LangChain (LCEL) adapter.

LCEL states composition with the pipe operator, and branching with
`RunnableBranch`. Both are readable from the syntax tree:

    classify_chain = classify_prompt | router_model | StrOutputParser()
    support_chain = RunnableBranch(
        (lambda x: x["topic"] == "billing", billing_chain),
        fallback_chain,
    )

A named chain is one step in the workflow. The prompt and model *inside* it are
components the generic pass already found, and are bound to the step rather than
becoming steps of their own -- a three-stage pipe is one thing a workflow does,
not three.

Not extracted: chains composed at runtime, `RunnableLambda` bodies, and the
legacy `LLMChain` classes. None of them states its structure in a readable form.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..analyze.python import (Finding, FileReport, _chain, _const_str,
                              iter_python_files)
from .base import AdapterResult

__all__ = ["LangChainAdapter"]

CONF_CHAIN = 0.92
CONF_BRANCH = 0.90
PROMPT_CTORS = ("ChatPromptTemplate", "PromptTemplate", "FewShotPromptTemplate",
                "ChatPromptValue", "HumanMessagePromptTemplate")


def _pipe_stages(node: ast.AST) -> list[ast.AST] | None:
    """Flatten `a | b | c` into its stages, or None if this is not a pipe."""
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)):
        return None
    left = _pipe_stages(node.left) or [node.left]
    right = _pipe_stages(node.right) or [node.right]
    return left + right


def _leaf(node: ast.AST) -> str | None:
    chain = _chain(node.func if isinstance(node, ast.Call) else node)
    return chain.rsplit(".", 1)[-1] if chain else None


class _Walker(ast.NodeVisitor):
    def __init__(self, path: str, module: str):
        self.path, self.module = path, module
        self.findings: list[Finding] = []
        self.notes: list[str] = []
        self.prompt_vars: dict[str, str] = {}   # variable -> template constant
        self.chains: set[str] = set()

    def add(self, kind, key, name, node, confidence, data):
        self.findings.append(Finding(
            kind=kind, key=key, name=name, file=self.path, line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno), symbol=self.module,
            confidence=confidence, data=data))

    def visit_Assign(self, node: ast.Assign):
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = node.value
        if targets:
            self._prompt_binding(targets[0], value)
            stages = _pipe_stages(value)
            if stages:
                self._chain(targets[0], stages, node)
            elif isinstance(value, ast.Call) and _leaf(value) == "RunnableBranch":
                self._branch(targets[0], value, node)
        self.generic_visit(node)

    def _prompt_binding(self, var: str, value: ast.AST):
        """`p = ChatPromptTemplate.from_template(TEMPLATE)` binds p to TEMPLATE."""
        if not isinstance(value, ast.Call):
            return
        chain = _chain(value.func)
        if not any(chain.startswith(c) or f".{c}" in chain for c in PROMPT_CTORS):
            return
        for arg in list(value.args) + [kw.value for kw in value.keywords]:
            if isinstance(arg, ast.Name):
                self.prompt_vars[var] = arg.id
                return

    def _chain(self, var: str, stages: list[ast.AST], node: ast.Assign):
        prompts, llm, parts = [], None, []
        for stage in stages:
            leaf = _leaf(stage)
            if leaf is None:
                continue
            parts.append(leaf)
            if leaf in self.prompt_vars:
                prompts.append(self.prompt_vars[leaf])
            elif llm is None and not leaf.endswith("Parser") \
                    and leaf not in ("RunnablePassthrough", "RunnableParallel"):
                llm = leaf
        self.chains.add(var)
        self.add("fw_node", f"fwnode:chain:{var}", var, node, CONF_CHAIN,
                 {"builder": "lcel", "label": var, "target": var,
                  "prompts": prompts, "llm": llm, "stages": parts})

    def _branch(self, var: str, call: ast.Call, node: ast.Assign):
        mapping: dict[str, str] = {}
        partial = False
        for index, arg in enumerate(call.args):
            if isinstance(arg, ast.Tuple) and len(arg.elts) == 2:
                condition, target = arg.elts
                name = _leaf(target)
                if name is None:
                    partial = True
                    continue
                mapping[self._condition_text(condition, index)] = name
            else:
                name = _leaf(arg)                       # trailing default branch
                if name is None:
                    partial = True
                else:
                    mapping["default"] = name
        if partial:
            self.notes.append(
                f"{self.path}:{node.lineno}: a RunnableBranch target is not a named "
                f"runnable; that route is missing from the graph")
        self.add("fw_branch", f"fwbranch:{var}", var, node,
                 CONF_BRANCH if mapping and not partial else 0.5,
                 {"builder": "lcel", "source": var, "router": var,
                  "mapping": mapping, "partial": partial})

    @staticmethod
    def _condition_text(node: ast.AST, index: int) -> str:
        """A readable label for a branch predicate."""
        try:
            text = ast.unparse(node.body if isinstance(node, ast.Lambda) else node)
        except Exception:                                # noqa: BLE001
            return f"branch {index + 1}"
        return text[:60]


class LangChainAdapter:
    name = "aiflow-adapter-langchain"
    framework = "langchain"

    def detects(self, reports: list[FileReport]) -> bool:
        return any(self.framework in r.frameworks for r in reports)

    def analyze(self, root: Path, reports: list[FileReport]) -> AdapterResult:
        findings: list[Finding] = []
        notes: list[str] = []
        root = Path(root)
        for path in iter_python_files(root):
            rel = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            walker = _Walker(rel, rel[:-3].replace("/", "."))
            walker.visit(tree)
            findings.extend(walker.findings)
            notes.extend(walker.notes)

        if not any(f.kind == "fw_branch" for f in findings):
            # Without a branch there is no declared ordering between named chains,
            # so there is no graph to report -- only components, which the generic
            # pass already found.
            if findings:
                notes.append("named LCEL chains found but no RunnableBranch; their "
                             "order is not declared in source and is not in the graph")
            return AdapterResult(self.name, [], notes=notes)

        routed = {t for f in findings if f.kind == "fw_branch"
                  for t in f.data["mapping"].values()}
        for f in [f for f in findings if f.kind == "fw_node" and f.data["label"] in routed]:
            findings.append(Finding(
                kind="fw_terminal", key=f"fwterm:{f.data['label']}", name=f.data["label"],
                file=f.file, line=f.line, symbol=f.symbol, confidence=0.8,
                data={"builder": "lcel", "label": f.data["label"]}))
        return AdapterResult(self.name, findings, notes=notes)
