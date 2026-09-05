"""OpenAI Agents SDK adapter.

Topology in this SDK is declarative: an `Agent(...)` names its tools and its
`handoffs`, and a handoff is an edge to another agent. `Runner.run(agent, ...)`
names the entry point.

    kb = Agent(name="KB", instructions=..., model="gpt-4o-mini", tools=[search])
    triage = Agent(name="Triage", handoffs=[kb, ticket])
    await Runner.run(triage, question)

Handoffs referenced by variable are resolved; anything built at runtime is
reported rather than guessed.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..analyze.python import (Finding, FileReport, _chain, _const_str, _kwarg,
                              iter_python_files)
from .base import AdapterResult

__all__ = ["OpenAIAgentsAdapter"]

CONF_AGENT = 0.94
CONF_HANDOFF = 0.94
CONF_ENTRY = 0.90


def _names(node: ast.AST | None) -> list[str]:
    """Variable names inside a list/tuple literal."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    out = []
    for element in node.elts:
        chain = _chain(element.func if isinstance(element, ast.Call) else element)
        if chain:
            out.append(chain.rsplit(".", 1)[-1])
    return out


class _Walker(ast.NodeVisitor):
    def __init__(self, path: str, module: str):
        self.path, self.module = path, module
        self.findings: list[Finding] = []
        self.notes: list[str] = []
        self.agents: set[str] = set()

    def add(self, kind, key, name, node, confidence, data):
        self.findings.append(Finding(
            kind=kind, key=key, name=name, file=self.path, line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno), symbol=self.module,
            confidence=confidence, data=data))

    def visit_Assign(self, node: ast.Assign):
        value = node.value
        if isinstance(value, ast.Call) and _chain(value.func).rsplit(".", 1)[-1] == "Agent":
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets:
                self._agent(targets[0], value, node)
        self.generic_visit(node)

    def _agent(self, var: str, call: ast.Call, node: ast.Assign):
        display = _const_str(_kwarg(call, "name")) or var
        instructions = _kwarg(call, "instructions")
        prompts = []
        if isinstance(instructions, ast.Name):
            prompts.append(instructions.id)

        model_arg = _kwarg(call, "model")
        model = _const_str(model_arg)
        llm_var = model_arg.id if isinstance(model_arg, ast.Name) else None

        self.agents.add(var)
        self.add("fw_node", f"fwnode:agent:{var}", var, node, CONF_AGENT,
                 {"builder": "agents", "label": var, "target": var,
                  "display": display, "tools": _names(_kwarg(call, "tools")),
                  "prompts": prompts, "model": model, "llm": llm_var,
                  "provider": "openai"})

        handoffs = _kwarg(call, "handoffs")
        if handoffs is not None and not isinstance(handoffs, (ast.List, ast.Tuple)):
            self.notes.append(
                f"{self.path}:{node.lineno}: handoffs for {var} are not a literal "
                f"list; those edges are missing from the graph")
        for target in _names(handoffs):
            self.add("fw_edge", f"fwedge:{var}:{target}", f"{var}->{target}", node,
                     CONF_HANDOFF,
                     {"builder": "agents", "source": var, "target": target})

    def visit_Call(self, node: ast.Call):
        chain = _chain(node.func)
        if chain.rsplit(".", 1)[-1] in ("run", "run_sync", "run_streamed") \
                and "Runner" in chain and node.args:
            entry = _chain(node.args[0])
            if entry:
                self.add("fw_entry", f"fwentry:{entry}", entry.rsplit(".", 1)[-1],
                         node, CONF_ENTRY,
                         {"builder": "agents", "label": entry.rsplit(".", 1)[-1]})
        self.generic_visit(node)


class OpenAIAgentsAdapter:
    name = "aiflow-adapter-openai-agents"
    framework = "openai-agents"

    def detects(self, reports: list[FileReport]) -> bool:
        return any(self.framework in r.frameworks for r in reports)

    def analyze(self, root: Path, reports: list[FileReport]) -> AdapterResult:
        findings: list[Finding] = []
        notes: list[str] = []
        agents: set[str] = set()
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
            agents |= walker.agents

        # An agent nothing hands off to is where a run ends.
        handed_to = {f.data["target"] for f in findings if f.kind == "fw_edge"}
        entries = {f.data["label"] for f in findings if f.kind == "fw_entry"}
        for f in [f for f in findings if f.kind == "fw_node"]:
            label = f.data["label"]
            if label in handed_to and label not in entries:
                findings.append(Finding(
                    kind="fw_terminal", key=f"fwterm:{label}", name=label,
                    file=f.file, line=f.line, symbol=f.symbol, confidence=0.75,
                    data={"builder": "agents", "label": label}))
        return AdapterResult(self.name, findings, notes=notes)
