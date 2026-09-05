"""Graph queries over an AIFLOW document.

These are the operations that make a `.aiflow` file worth more than a
diagram: they answer behavioral questions about the workflow directly,
without re-reading the source project.

Each named query below maps to one of the questions the format is meant to
answer -- "where is RAG used", "what happens if the vector DB fails",
"show all paths to the final response", "which agents use external tools",
"find missing error handling".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .model import Document, Edge, Node

__all__ = ["Graph", "Path", "Finding"]

MAX_PATHS = 1000


@dataclass(frozen=True)
class Path:
    """A route through the workflow, alternating nodes and the edges taken."""
    nodes: tuple[str, ...]
    edges: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.nodes)

    def render(self, doc: Document) -> str:
        parts = []
        for i, nid in enumerate(self.nodes):
            n = doc.node(nid)
            parts.append(f"{nid}({n.type})" if n else nid)
            if i < len(self.edges):
                e = doc.edge(self.edges[i])
                parts.append(f"--{e.type if e else '?'}-->")
        return " ".join(parts)


@dataclass(frozen=True)
class Finding:
    """A behavioral observation about the workflow, with its origin."""
    kind: str
    subject: str
    detail: str
    source: str | None = None
    inferred: bool = False
    """True when the observation rests on ai_context a model produced rather
    than on anything parsed from code. Consumers should present the two
    differently; collapsing them is how inferred structure gets trusted."""


class Graph:
    """Adjacency view over a Document.

    The document remains the source of truth; this class only indexes it.
    Build a new Graph after mutating the document.
    """

    def __init__(self, doc: Document):
        self.doc = doc
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        for e in doc.edges:
            self._out[e.source].append(e)
            self._in[e.target].append(e)

    # -- basic traversal --------------------------------------------------
    def out_edges(self, node_id: str, *types: str) -> list[Edge]:
        edges = self._out.get(node_id, [])
        return [e for e in edges if e.type in types] if types else list(edges)

    def in_edges(self, node_id: str, *types: str) -> list[Edge]:
        edges = self._in.get(node_id, [])
        return [e for e in edges if e.type in types] if types else list(edges)

    def successors(self, node_id: str, *types: str) -> list[Node]:
        return [self.doc.node(e.target) for e in self.out_edges(node_id, *types)]

    def predecessors(self, node_id: str, *types: str) -> list[Node]:
        return [self.doc.node(e.source) for e in self.in_edges(node_id, *types)]

    def nodes_of_type(self, *types: str) -> list[Node]:
        return [n for n in self.doc.nodes if n.type in types]

    def entry_points(self) -> list[Node]:
        return self.nodes_of_type("input")

    def terminals(self) -> list[Node]:
        return self.nodes_of_type("output")

    # -- reachability -----------------------------------------------------
    def reachable_from(self, *node_ids: str) -> set[str]:
        seen, stack = set(node_ids), list(node_ids)
        while stack:
            for e in self._out.get(stack.pop(), ()):
                if e.target not in seen:
                    seen.add(e.target)
                    stack.append(e.target)
        return seen

    def unreachable(self) -> list[Node]:
        """Nodes no input can reach -- dead branches, or a missing edge."""
        entries = [n.id for n in self.entry_points()]
        if not entries:
            return []
        live = self.reachable_from(*entries)
        return [n for n in self.doc.nodes if n.id not in live]

    def paths(self, source: str, target: str, limit: int = MAX_PATHS) -> list[Path]:
        """All simple paths between two nodes. Cycles are not traversed twice."""
        found: list[Path] = []

        def walk(cur: str, nodes: tuple, edges: tuple, visited: frozenset):
            if len(found) >= limit:
                return
            if cur == target and len(nodes) > 0:
                found.append(Path(nodes, edges))
                return
            for e in self._out.get(cur, ()):
                if e.target in visited:
                    continue
                walk(e.target, nodes + (e.target,), edges + (e.id,), visited | {e.target})

        walk(source, (source,), (), frozenset({source}))
        return found

    def paths_to_outputs(self, limit: int = MAX_PATHS) -> list[Path]:
        """Every route from an entry point to a terminal.

        Answers: "show all paths to the final response".
        """
        out: list[Path] = []
        for entry in self.entry_points():
            for term in self.terminals():
                out.extend(self.paths(entry.id, term.id, limit - len(out)))
                if len(out) >= limit:
                    return out
        return out

    # -- named behavioral queries -----------------------------------------
    def rag_components(self) -> list[Node]:
        """Retrieval surface of the workflow.

        Answers: "where is RAG used?".
        """
        direct = self.nodes_of_type("retriever", "vector_store")
        via_edges = {e.source for e in self.doc.edges if e.type == "retrieves"}
        via_edges |= {e.target for e in self.doc.edges if e.type == "retrieves"}
        ids = {n.id for n in direct} | via_edges
        return [n for n in self.doc.nodes if n.id in ids]

    def agents_using_tools(self) -> dict[str, list[Node]]:
        """Agents mapped to the tool nodes they can cause to be invoked.

        A tool is attributed to its *nearest controlling agent*: the walk
        proceeds downstream and stops at the next agent, so a tool reached
        through an intermediate LLM or condition is still credited to the
        agent that drives that branch, and is not also credited to every
        agent upstream of it.

        Answers: "which agents use external tools?".
        """
        result: dict[str, list[Node]] = {}
        for agent in self.nodes_of_type("agent"):
            tools: dict[str, Node] = {}
            seen, stack = {agent.id}, [agent.id]
            while stack:
                for e in self._out.get(stack.pop(), ()):
                    nxt = self.doc.node(e.target)
                    if nxt is None or nxt.id in seen:
                        continue
                    seen.add(nxt.id)
                    if nxt.type == "agent":
                        continue  # control has passed on; not this agent's tool
                    if nxt.type == "tool":
                        tools[nxt.id] = nxt
                    stack.append(nxt.id)
            if tools:
                result[agent.id] = list(tools.values())
        return result

    def unhandled_failures(self) -> list[Finding]:
        """Declared failure modes with no handler.

        Answers: "find missing error handling".
        """
        out: list[Finding] = []
        carriers = (list(self.doc.nodes) + self.doc.registry("tools")
                    + self.doc.registry("data_sources"))
        for item in carriers:
            ctx = getattr(item, "ai_context", None)
            inferred = bool(ctx and ctx.provenance and ctx.provenance.is_inferred
                            and not ctx.provenance.reviewed_by)
            for fm in (ctx.failure_modes if ctx and ctx.failure_modes else ()):
                if fm.handled is False or (fm.handled is None and not fm.handler_node):
                    out.append(Finding("unhandled_failure", item.id, fm.description,
                                       str(fm.source) if fm.source else None,
                                       inferred=inferred))
        return out

    def impact_of(self, node_id: str) -> list[Node]:
        """Everything downstream of a node -- what stops working if it fails.

        Answers: "what happens if the vector DB fails?".
        """
        downstream = self.reachable_from(node_id) - {node_id}
        # a store is reached *by* a retriever, so failure also propagates upstream
        # through the retrieval relationship
        for e in self.in_edges(node_id, "retrieves", "uses"):
            downstream |= self.reachable_from(e.source)
        downstream.discard(node_id)
        return [n for n in self.doc.nodes if n.id in downstream]

    def low_trust(self, threshold: float = 0.60) -> list[Finding]:
        """Claims that were inferred rather than parsed, and not yet reviewed.

        This is the query that keeps AI-generated structure accountable.
        """
        out: list[Finding] = []
        buckets = [("node", self.doc.nodes), ("edge", self.doc.edges)]
        buckets += [(k[:-1], self.doc.registry(k))
                    for k in ("prompts", "models", "tools", "data_sources")]
        for kind, items in buckets:
            for item in items:
                # An element's own provenance and its ai_context's provenance are
                # separate claims; either can be the inferred one.
                for label, p in ((kind, getattr(item, "provenance", None)),
                                 (f"{kind}.ai_context",
                                  getattr(getattr(item, "ai_context", None),
                                          "provenance", None))):
                    if p is None or not p.is_inferred or p.reviewed_by:
                        continue
                    conf = p.confidence
                    reasons = []
                    if conf is not None and conf < threshold:
                        reasons.append(f"confidence {conf}")
                    if not p.evidence:
                        reasons.append("no evidence")
                    if reasons:
                        out.append(Finding("low_trust", f"{label}:{item.id}",
                                           "; ".join(reasons), p.notes, inferred=True))
        return out

    # -- summary ----------------------------------------------------------
    def summary(self) -> dict:
        by_node = defaultdict(int)
        for n in self.doc.nodes:
            by_node[n.type] += 1
        by_edge = defaultdict(int)
        for e in self.doc.edges:
            by_edge[e.type] += 1
        inferred = sum(1 for i in list(self.doc.nodes) + list(self.doc.edges)
                       if getattr(i, "provenance", None) and i.provenance.is_inferred)
        return {
            "nodes": len(self.doc.nodes), "edges": len(self.doc.edges),
            "node_types": dict(sorted(by_node.items())),
            "edge_types": dict(sorted(by_edge.items())),
            "registries": {k: len(self.doc.registry(k))
                           for k in ("prompts", "models", "tools", "data_sources")},
            "entry_points": [n.id for n in self.entry_points()],
            "terminals": [n.id for n in self.terminals()],
            "paths_to_output": len(self.paths_to_outputs()),
            "inferred_elements": inferred,
        }
