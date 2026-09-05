"""Natural-language questions about a workflow.

Two answer paths, deliberately in this order:

1. **From the graph.** Many of the questions people actually ask -- where is
   RAG used, which agents call tools, what breaks if this store fails, show me
   the paths -- are graph queries with exact answers. Those are computed, cost
   nothing, and cannot be wrong about the document.
2. **From a model**, only when the question does not match a known query.

The ordering is not just frugality, though on a 50-call-a-day tier that matters.
An exact answer is better than a fluent one, and routing "which agents use
tools?" through a model would replace a fact with a guess.

Model answers are grounded in a digest of the document and are told to say when
the document does not contain the answer. They are labelled `ai_inference`;
computed answers are labelled `graph`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..graph import Graph
from ..model import Document
from .budget import Cache, Ledger
from .client import OpenRouterClient, extract_json

__all__ = ["ask", "Answer", "PROMPT_VERSION"]

PROMPT_VERSION = "1"

SYSTEM = """You answer questions about an AI workflow, using only the workflow
description you are given.

Return ONLY a JSON object, no prose and no markdown fence:

{"answer": "your answer",
 "cites": ["node or edge ids you relied on"],
 "confidence": 0.0-1.0,
 "grounded": true|false}

Rules:
1. Use only the supplied description. Do not draw on general knowledge about
   how such systems are usually built.
2. If the description does not contain the answer, set "grounded": false and say
   plainly what is missing. That is a correct answer, not a failure.
3. Cite the ids you used. An uncited claim is a guess.
4. Note when something you rely on is marked ai_inference — that part of the
   description was itself inferred, not parsed from code.
5. Be specific and brief. No preamble."""


@dataclass
class Answer:
    text: str
    source: str                      # "graph" or a model id
    cites: list[str] = field(default_factory=list)
    confidence: float | None = None
    grounded: bool = True
    calls_made: int = 0
    matched: str | None = None       # the deterministic query that answered it

    @property
    def computed(self) -> bool:
        return self.source == "graph"


# ---------------------------------------------------------------------------
# deterministic answers
# ---------------------------------------------------------------------------
def _paths(doc: Document, g: Graph, _m) -> Answer:
    paths = g.paths_to_outputs()
    if not paths:
        return Answer("No path runs from an input node to an output node.",
                      "graph", matched="paths")
    lines = [f"{len(paths)} path(s) from input to output:"]
    lines += [f"  {i + 1}. {p.render(doc)}" for i, p in enumerate(paths)]
    return Answer("\n".join(lines), "graph",
                  cites=sorted({n for p in paths for n in p.nodes}), matched="paths")


def _rag(doc: Document, g: Graph, _m) -> Answer:
    parts = g.rag_components()
    if not parts:
        return Answer("This workflow has no retrieval components.", "graph", matched="rag")
    lines = ["Retrieval surface:"]
    for n in parts:
        entry = doc.resolve(n)
        detail = ""
        if entry is not None:
            bits = [entry.provider or "", json.dumps(entry.config) if getattr(entry, "config", None) else ""]
            detail = "  " + " ".join(b for b in bits if b)
        lines.append(f"  {n.id} ({n.type}){detail}")
    return Answer("\n".join(lines), "graph", cites=[n.id for n in parts], matched="rag")


def _tools(doc: Document, g: Graph, _m) -> Answer:
    mapping = g.agents_using_tools()
    if not mapping:
        return Answer("No agent in this workflow invokes a tool.", "graph", matched="tools")
    lines = ["Agents and the tools they can invoke:"]
    cites = []
    for agent, tools in mapping.items():
        lines.append(f"  {agent} -> {', '.join(t.id for t in tools)}")
        cites += [agent] + [t.id for t in tools]
    return Answer("\n".join(lines), "graph", cites=cites, matched="tools")


def _failures(doc: Document, g: Graph, _m) -> Answer:
    findings = g.unhandled_failures()
    if not findings:
        return Answer("No declared failure mode is missing a handler.",
                      "graph", matched="failures")
    lines = ["Failure modes with no handler:"]
    for f in findings:
        tag = " [inferred]" if f.inferred else ""
        where = f" ({f.source})" if f.source else ""
        lines.append(f"  {f.subject}{tag}: {f.detail}{where}")
    if any(f.inferred for f in findings):
        lines.append("\nEntries marked [inferred] rest on ai_context a model produced, "
                     "not on anything parsed from code.")
    return Answer("\n".join(lines), "graph",
                  cites=[f.subject for f in findings], matched="failures")


def _trust(doc: Document, g: Graph, _m) -> Answer:
    findings = g.low_trust()
    inferred = [x.id for x in list(doc.nodes) + list(doc.edges)
                if getattr(x, "provenance", None) and x.provenance.is_inferred]
    inferred += [n.id for n in doc.nodes
                 if n.ai_context and n.ai_context.provenance
                 and n.ai_context.provenance.is_inferred]
    if not inferred:
        return Answer("Nothing in this document was AI-inferred; every claim was "
                      "parsed or authored.", "graph", matched="trust")
    lines = [f"{len(inferred)} element(s) carry AI-inferred claims:"]
    lines += [f"  {i}" for i in sorted(set(inferred))]
    if findings:
        lines.append("\nOf those, these fall below the review threshold or cite no evidence:")
        lines += [f"  {f.subject}: {f.detail}" for f in findings]
    return Answer("\n".join(lines), "graph", cites=sorted(set(inferred)), matched="trust")


def _impact(doc: Document, g: Graph, match) -> Answer | None:
    name = match.group("target").strip().strip("?.'\"`")
    node = _resolve_node(doc, name)
    if node is None:
        return None
    affected = g.impact_of(node.id)
    if not affected:
        return Answer(f"Nothing downstream depends on {node.id}.", "graph", matched="impact")
    outputs = [n.id for n in affected if n.type == "output"]
    lines = [f"If {node.id} ({node.type}) fails, {len(affected)} component(s) are affected:"]
    lines += [f"  {n.id} ({n.type})" for n in affected]
    if outputs:
        lines.append(f"\nThe failure reaches {', '.join(outputs)}, so there is no "
                     f"alternate path to a result.")
    ctx = node.ai_context
    for fm in (ctx.failure_modes if ctx and ctx.failure_modes else ()):
        lines.append(f"\nDeclared failure mode: {fm.description}")
    return Answer("\n".join(lines), "graph",
                  cites=[node.id] + [n.id for n in affected], matched="impact")


def _describe(doc: Document, g: Graph, match) -> Answer | None:
    name = match.group("target").strip().strip("?.'\"`")
    node = _resolve_node(doc, name)
    if node is None:
        return None
    lines = [f"{node.id} ({node.type})" + (f" — {node.name}" if node.name else "")]
    if node.description:
        lines.append(node.description)
    ctx = node.ai_context
    if ctx and ctx.summary:
        marker = " [inferred]" if ctx.provenance and ctx.provenance.is_inferred else ""
        lines.append(f"Summary{marker}: {ctx.summary}")
    entry = doc.resolve(node)
    if entry is not None:
        lines.append(f"Definition: {entry.id}"
                     + (f" ({entry.provider}/{entry.model})" if getattr(entry, "model", None) else ""))
    for s in node.source or ():
        lines.append(f"Source: {s}" + (f"  {s.symbol}" if s.symbol else ""))
    for e in g.in_edges(node.id):
        lines.append(f"In:  {e.source} --{e.type}-->")
    for e in g.out_edges(node.id):
        lines.append(f"Out: --{e.type}--> {e.target}"
                     + (f"  when {e.when}" if e.when else ""))
    return Answer("\n".join(lines), "graph", cites=[node.id], matched="describe")


def _summary(doc: Document, g: Graph, _m) -> Answer:
    s = g.summary()
    lines = [f"{s['nodes']} nodes, {s['edges']} edges, "
             f"{s['paths_to_output']} path(s) to output.",
             "  nodes  " + ", ".join(f"{k}={v}" for k, v in s["node_types"].items()),
             "  edges  " + ", ".join(f"{k}={v}" for k, v in s["edge_types"].items())]
    if s["inferred_elements"]:
        lines.append(f"  {s['inferred_elements']} element(s) carry AI-inferred claims.")
    return Answer("\n".join(lines), "graph", matched="summary")


def _resolve_node(doc: Document, name: str):
    lowered = name.lower()
    for node in doc.nodes:
        if node.id.lower() == lowered:
            return node
    for node in doc.nodes:
        if (node.name or "").lower() == lowered:
            return node
    candidates = [n for n in doc.nodes
                  if lowered in n.id.lower() or lowered in (n.name or "").lower()]
    return candidates[0] if len(candidates) == 1 else None


# Ordered: the first pattern that matches wins, so specific forms precede generic.
RULES = [
    (re.compile(r"\bif\s+(?:the\s+)?(?P<target>[\w.:-]{2,})\s+"
                r"(?:fails?|breaks?|errors?|goes? down|is (?:down|unavailable|unreachable))",
                re.I), _impact),
    (re.compile(r"\b(?:blast radius|impact|downstream)\s+(?:of|from)\s+"
                r"(?:the\s+)?(?P<target>[\w.:-]{2,})", re.I), _impact),
    (re.compile(r"\bwhat (?:depends on|breaks without)\s+(?:the\s+)?(?P<target>[\w.:-]{2,})",
                re.I), _impact),
    (re.compile(r"\b(?:missing|no|without|find).{0,20}error handling\b|\bunhandled\b"
                r"|\bwhat can (?:go wrong|fail)\b|\bfailure modes?\b", re.I), _failures),
    (re.compile(r"\b(?:rag|retriev\w*|vector (?:db|store|database)|embedding)\b", re.I), _rag),
    (re.compile(r"\btools?\b", re.I), _tools),
    (re.compile(r"\b(?:paths?|routes?|flow|end.to.end|reach the (?:output|response|answer))\b",
                re.I), _paths),
    (re.compile(r"\b(?:inferred|guess\w*|hallucinat\w*|trust\w*|confidence|provenance)\b",
                re.I), _trust),
    (re.compile(r"^\s*(?:what|which) (?:is|are|does)\s+(?:the\s+)?(?P<target>[\w.:-]{3,})",
                re.I), _describe),
    (re.compile(r"\b(?:explain|describe|tell me about)\s+(?:the\s+)?(?P<target>[\w.:-]{3,})",
                re.I), _describe),
    (re.compile(r"\b(?:summar\w+|overview|how many|what.s in|structure)\b", re.I), _summary),
]


def answer_from_graph(doc: Document, question: str) -> Answer | None:
    """Try to answer exactly, without a model."""
    g = Graph(doc)
    for pattern, handler in RULES:
        match = pattern.search(question)
        if not match:
            continue
        try:
            result = handler(doc, g, match)
        except Exception:                    # noqa: BLE001 - fall through to the model
            return None
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# model answers
# ---------------------------------------------------------------------------
def digest(doc: Document, limit: int = 20000) -> dict:
    """A compact, self-contained description of the workflow for a model."""
    g = Graph(doc)
    out = {
        "project": (doc.project.name if doc.project else None),
        "nodes": [], "edges": [],
        "analysis": {
            "paths": [list(p.nodes) for p in g.paths_to_outputs()][:12],
            "unhandled_failures": [{"component": f.subject, "detail": f.detail,
                                    "inferred": f.inferred}
                                   for f in g.unhandled_failures()],
        },
    }
    for n in doc.nodes:
        item = {"id": n.id, "type": n.type}
        if n.name:
            item["name"] = n.name
        if n.description:
            item["description"] = n.description
        entry = doc.resolve(n)
        if entry is not None:
            item["definition"] = {k: v for k, v in (
                ("id", entry.id), ("provider", getattr(entry, "provider", None)),
                ("model", getattr(entry, "model", None)),
                ("kind", getattr(entry, "kind", None))) if v}
        if n.source:
            item["source"] = str(n.source[0])
        ctx = n.ai_context
        if ctx:
            item["context"] = {k: v for k, v in (
                ("summary", ctx.summary), ("intent", ctx.intent),
                ("invariants", ctx.invariants),
                ("failure_modes", [f.description for f in ctx.failure_modes or ()])) if v}
            if ctx.provenance and ctx.provenance.is_inferred:
                item["context"]["_note"] = "this description was ai_inference, not parsed"
        out["nodes"].append(item)

    for e in doc.edges:
        item = {"id": e.id, "type": e.type, "from": e.source, "to": e.target}
        if e.when:
            item["when"] = e.when
        out["edges"].append(item)

    blob = json.dumps(out)
    if len(blob) > limit:                     # drop the least load-bearing detail first
        for n in out["nodes"]:
            n.pop("source", None)
        if len(json.dumps(out)) > limit:
            out["analysis"].pop("paths", None)
    return out


def ask(doc: Document, question: str, *, client: OpenRouterClient | None = None,
        ledger: Ledger | None = None, cache: Cache | None = None,
        allow_model: bool = True) -> Answer:
    """Answer a question about the workflow, preferring the exact path."""
    computed = answer_from_graph(doc, question)
    if computed is not None:
        return computed

    if not allow_model:
        return Answer(
            "That question does not match a query this tool can compute, and the "
            "model path is disabled. Try asking about paths, retrieval, tools, "
            "failure handling, inferred claims, or a specific component.",
            "graph", grounded=False)

    if client is None:
        raise ValueError("ask() needs a client for questions the graph cannot answer")

    payload = {"question": question, "workflow": digest(doc)}
    cache = cache or Cache.open()
    key = Cache.key(client.model, PROMPT_VERSION, payload)
    hit = cache.get(key)

    calls = 0
    if hit is None:
        ledger = ledger or Ledger.open()
        ledger.check(1)
        raw, finish = client.complete(SYSTEM, json.dumps(payload, indent=2))
        ledger.record(1)
        calls = 1
        try:
            hit = extract_json(raw)
        except Exception:                     # noqa: BLE001 - reported to the caller
            if finish == "length":
                return Answer("The model's reply hit the token limit and arrived "
                              "truncated. Try a narrower question.",
                              client.model, grounded=False, calls_made=calls)
            return Answer(f"The model did not return a usable answer:\n{raw[:300]}",
                          client.model, grounded=False, calls_made=calls)
        cache.put(key, hit)

    text = hit.get("answer") if isinstance(hit, dict) else None
    if not isinstance(text, str) or not text.strip():
        return Answer("The model returned no answer.", client.model,
                      grounded=False, calls_made=calls)

    cites = hit.get("cites")
    known = {n.id for n in doc.nodes} | {e.id for e in doc.edges}
    # a citation to something not in the document is not a citation
    cites = [c for c in cites if isinstance(c, str) and c in known] if isinstance(cites, list) else []

    try:
        confidence = min(1.0, max(0.0, float(hit.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.5

    return Answer(text.strip(), client.model, cites=cites, confidence=confidence,
                  grounded=bool(hit.get("grounded", True)), calls_made=calls)
