"""CrewAI adapter.

A crew's topology lives in its tasks, not its agents: `Task(...)` declares which
agent performs it and, through `context=`, which tasks must finish first. A
`Crew(process=Process.sequential)` runs its task list in order.

Tasks are therefore the graph steps, and each carries the model and tools of the
agent assigned to it. Agents are roles, not positions in the flow -- two tasks
handled by the same agent are two steps.

`Process.hierarchical` delegates ordering to a manager at runtime, which no AST
walk can resolve; the adapter says so instead of inventing a sequence.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..analyze.python import (Finding, FileReport, _chain, _const_str, _kwarg,
                              iter_python_files)
from .base import AdapterResult

__all__ = ["CrewAIAdapter"]

CONF_TASK = 0.93
CONF_SEQUENCE = 0.92
CONF_CONTEXT = 0.94


def _var_names(node: ast.AST | None) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    return [n.id for n in node.elts if isinstance(n, ast.Name)]


class _Walker(ast.NodeVisitor):
    def __init__(self, path: str, module: str):
        self.path, self.module = path, module
        self.findings: list[Finding] = []
        self.notes: list[str] = []
        self.agents: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.crews: list[tuple[list[str], str | None, ast.AST]] = []

    def add(self, kind, key, name, node, confidence, data):
        self.findings.append(Finding(
            kind=kind, key=key, name=name, file=self.path, line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno), symbol=self.module,
            confidence=confidence, data=data))

    def visit_Assign(self, node: ast.Assign):
        value = node.value
        if isinstance(value, ast.Call):
            ctor = _chain(value.func).rsplit(".", 1)[-1]
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets and ctor == "Agent":
                self._agent(targets[0], value)
            elif targets and ctor == "Task":
                self._task(targets[0], value, node)
            elif ctor == "Crew":
                self._crew(value, node)
        self.generic_visit(node)

    def _agent(self, var: str, call: ast.Call):
        llm_arg = _kwarg(call, "llm")
        self.agents[var] = {
            "role": _const_str(_kwarg(call, "role")),
            "goal_const": (_kwarg(call, "goal").id
                           if isinstance(_kwarg(call, "goal"), ast.Name) else None),
            "tools": _var_names(_kwarg(call, "tools")),
            "model": _const_str(llm_arg),
            "llm": llm_arg.id if isinstance(llm_arg, ast.Name) else None,
        }

    def _task(self, var: str, call: ast.Call, node: ast.Assign):
        agent_arg = _kwarg(call, "agent")
        agent = agent_arg.id if isinstance(agent_arg, ast.Name) else None
        self.tasks[var] = {"agent": agent, "context": _var_names(_kwarg(call, "context")),
                           "description": _const_str(_kwarg(call, "description")),
                           "node": node, "file": self.path, "module": self.module}

    def _crew(self, call: ast.Call, node: ast.Assign):
        tasks_arg = _kwarg(call, "tasks")
        process = _chain(_kwarg(call, "process") or ast.Constant(None))
        if tasks_arg is not None and not isinstance(tasks_arg, (ast.List, ast.Tuple)):
            self.notes.append(
                f"{self.path}:{node.lineno}: the crew's task list is not a literal; "
                f"the execution order is missing from the graph")
        self.crews.append((_var_names(tasks_arg), process, node))


class CrewAIAdapter:
    name = "aiflow-adapter-crewai"
    framework = "crewai"

    def detects(self, reports: list[FileReport]) -> bool:
        return any(self.framework in r.frameworks for r in reports)

    def analyze(self, root: Path, reports: list[FileReport]) -> AdapterResult:
        findings: list[Finding] = []
        notes: list[str] = []
        agents: dict[str, dict] = {}
        tasks: dict[str, dict] = {}
        crews: list = []
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
            agents.update(walker.agents)
            tasks.update(walker.tasks)
            crews.extend([(t, p, n, rel, rel[:-3].replace('/', '.'))
                          for t, p, n in walker.crews])

        def emit(kind, key, name, rel, module, node, confidence, data):
            findings.append(Finding(
                kind=kind, key=key, name=name, file=rel, line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno), symbol=module,
                confidence=confidence, data=data))

        for var, task in tasks.items():
            agent = agents.get(task["agent"] or "", {})
            node = task["node"]
            emit("fw_node", f"fwnode:task:{var}", var, task["file"], task["module"],
                 node, CONF_TASK,
                 {"builder": "crew", "label": var, "target": var,
                  "display": task["description"] or var,
                  "agent_role": agent.get("role"),
                  "tools": agent.get("tools") or [],
                  "prompts": [g for g in (agent.get("goal_const"),) if g],
                  "model": agent.get("model"), "llm": agent.get("llm"),
                  "provider": "unknown"})

            for upstream in task["context"]:
                if upstream in tasks:
                    emit("fw_edge", f"fwedge:{upstream}:{var}", f"{upstream}->{var}",
                         task["file"], task["module"], node, CONF_CONTEXT,
                         {"builder": "crew", "source": upstream, "target": var})

        for task_list, process, node, rel, module in crews:
            if process and "hierarchical" in process:
                notes.append(
                    f"{rel}:{node.lineno}: Process.hierarchical delegates ordering to a "
                    f"manager at runtime; the sequence is not in the graph")
                continue
            known = [t for t in task_list if t in tasks]
            declared = {(e.data["source"], e.data["target"])
                        for e in findings if e.kind == "fw_edge"}
            for earlier, later in zip(known, known[1:]):
                if (earlier, later) not in declared:   # context= already said it
                    emit("fw_edge", f"fwedge:seq:{earlier}:{later}",
                         f"{earlier}->{later}", rel, module, node, CONF_SEQUENCE,
                         {"builder": "crew", "source": earlier, "target": later})
            if known:
                emit("fw_entry", f"fwentry:{known[0]}", known[0], rel, module, node,
                     CONF_SEQUENCE, {"builder": "crew", "label": known[0]})
                emit("fw_terminal", f"fwterm:{known[-1]}", known[-1], rel, module, node,
                     CONF_SEQUENCE, {"builder": "crew", "label": known[-1]})

        return AdapterResult(self.name, findings, notes=notes)
