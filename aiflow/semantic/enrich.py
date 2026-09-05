"""Semantic enrichment: the first component permitted to emit `ai_inference`.

The static analyzer deliberately emits no `ai_context` -- it can see structure
but not intent. This module fills that gap with a model, and the entire design
is arranged so the result stays distinguishable from parsed fact:

* Everything written carries `ai_context.provenance` with method
  `ai_inference` and a required confidence. The node's own provenance is left
  alone, so enriching a statically-parsed node never downgrades it.
* Existing context is never overwritten unless explicitly asked, so a human
  correction survives a re-run.
* The model is given the code locations it is reasoning about and told to omit
  anything it cannot support, rather than filling every field.

Cost control is structural: nodes are batched so one request covers many, and
each batch is cached by content so an unchanged workflow re-runs for free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..graph import Graph
from ..model import AIContext, Document, FailureMode, Provenance, SourceRef
from .budget import Cache, Ledger
from .client import OpenRouterClient, extract_json

__all__ = ["enrich", "plan", "EnrichResult", "PROMPT_VERSION"]

PROMPT_VERSION = "1"
GENERATOR = {"name": "aiflow-semantic-analyzer", "version": "0.1.0"}
# Sized so a full response fits inside the token limit. Truncation was the
# only failure seen in practice, and it costs a call to discover.
BATCH_CHARS = 4000
MAX_BATCH_NODES = 5

SYSTEM = """You analyse AI workflow graphs and describe what each component does.

You will receive components of one workflow, each with its type, name, code
location, configuration, and its connections to other components.

Return ONLY a JSON object, no prose and no markdown fence:

{"nodes": {"<node id>": {
  "summary": "one sentence on what this component does",
  "intent": "why it exists in this workflow",
  "failure_modes": [{"description": "a specific way this can fail"}],
  "invariants": ["a condition that should hold whenever it runs"],
  "side_effects": ["an observable effect outside the workflow"],
  "confidence": 0.0-1.0
}}}

Rules, in order of importance:

1. Do not invent. If the input does not support a field, omit that field. If it
   supports nothing about a component, omit the component entirely. A short,
   correct answer is worth more than a complete, speculative one.
2. `confidence` states how well the INPUT supports your description, not how
   fluent it is. Use below 0.5 when you are largely guessing from the name.
3. Never claim a failure is handled or unhandled. You cannot see error handling
   from this input. Describe the failure only.
4. Failure modes must be specific to this component and its connections, not
   generic ("the network may fail" is useless; "the vector store may return
   fewer than top_k documents, leaving the answer ungrounded" is useful).
5. Keep every string under 200 characters."""


@dataclass
class EnrichResult:
    document: Document
    enriched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    batches: int = 0
    calls_made: int = 0
    cache_hits: int = 0
    notes: list[str] = field(default_factory=list)


def _describe(doc: Document, g: Graph, node) -> dict:
    """Everything the model is allowed to reason from, and nothing more."""
    out: dict = {"id": node.id, "type": node.type}
    if node.name:
        out["name"] = node.name
    if node.description:
        out["description"] = node.description
    if node.config:
        out["config"] = node.config

    entry = doc.resolve(node)
    if entry is not None:
        definition = {"id": entry.id}
        for attr in ("provider", "model", "kind", "name", "description", "template"):
            value = getattr(entry, attr, None)
            if isinstance(value, str):
                definition[attr] = value[:400]
        for attr in ("config", "parameters"):
            value = getattr(entry, attr, None)
            if value:
                definition[attr] = value
        out["definition"] = definition

    if node.source:
        out["source"] = [str(s) + (f" {s.symbol}" if s.symbol else "") for s in node.source]
    incoming = [f"{e.source} --{e.type}-->" for e in g.in_edges(node.id)]
    outgoing = [f"--{e.type}--> {e.target}" for e in g.out_edges(node.id)]
    if incoming:
        out["incoming"] = incoming
    if outgoing:
        out["outgoing"] = outgoing
    return out


def plan(doc: Document, *, overwrite: bool = False) -> list[list[dict]]:
    """Group the nodes needing enrichment into request-sized batches.

    Returns batches of node descriptions. Length is the number of API calls the
    run will cost before caching is taken into account.
    """
    g = Graph(doc)
    pending = []
    for node in doc.nodes:
        if node.ai_context is not None and not overwrite:
            continue
        pending.append(_describe(doc, g, node))

    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for item in pending:
        blob = len(json.dumps(item))
        if current and (size + blob > BATCH_CHARS or len(current) >= MAX_BATCH_NODES):
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += blob
    if current:
        batches.append(current)
    return batches


def _context_from(payload: dict, node, model: str) -> AIContext | None:
    """Build an AIContext, dropping anything the model was not asked for."""
    confidence = payload.get("confidence")
    try:
        confidence = round(float(confidence), 2)
    except (TypeError, ValueError):
        confidence = 0.5                     # unstated confidence is not high confidence
    confidence = min(1.0, max(0.0, confidence))

    def strings(key):
        raw = payload.get(key)
        if not isinstance(raw, list):
            return None
        cleaned = [str(x)[:200] for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]
        return cleaned or None

    failures = None
    raw_failures = payload.get("failure_modes")
    if isinstance(raw_failures, list):
        collected = []
        for item in raw_failures:
            text = item.get("description") if isinstance(item, dict) else item
            if isinstance(text, str) and text.strip():
                # `handled` is deliberately not accepted from the model: it cannot
                # see error handling from the input it was given.
                collected.append(FailureMode(description=text.strip()[:200]))
        failures = collected or None

    summary = payload.get("summary")
    intent = payload.get("intent")
    context = AIContext(
        summary=str(summary)[:200] if isinstance(summary, str) and summary.strip() else None,
        intent=str(intent)[:200] if isinstance(intent, str) and intent.strip() else None,
        failure_modes=failures,
        invariants=strings("invariants"),
        side_effects=strings("side_effects"),
        provenance=Provenance(
            method="ai_inference", confidence=confidence,
            generator={**GENERATOR, "model": model},
            generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            evidence=[SourceRef(file=s.file, start_line=s.start_line,
                                end_line=s.end_line, symbol=s.symbol)
                      for s in (node.source or [])] or None,
            notes="Inferred from the workflow graph and code locations, not from "
                  "reading the source.",
        ),
    )
    if not any([context.summary, context.intent, context.failure_modes,
                context.invariants, context.side_effects]):
        return None
    return context


def enrich(doc: Document, *, client: OpenRouterClient | None = None,
           ledger: Ledger | None = None, cache: Cache | None = None,
           overwrite: bool = False, dry_run: bool = False,
           limit: int | None = None) -> EnrichResult:
    """Attach inferred semantics to every node lacking them."""
    batches = plan(doc, overwrite=overwrite)
    if limit is not None:
        batches = batches[:limit]

    result = EnrichResult(document=doc, batches=len(batches))
    if not batches:
        result.notes.append("Every node already has ai_context; nothing to do.")
        return result

    cache = cache or Cache.open()
    model = client.model if client else "(dry run)"

    # Determine how many batches actually need a call, so the ledger is only
    # charged for cache misses.
    prepared = []
    for batch in batches:
        key = Cache.key(model, PROMPT_VERSION, batch)
        prepared.append((batch, key, cache.get(key)))
    misses = sum(1 for _, _, hit in prepared if hit is None)

    if dry_run:
        result.cache_hits = len(prepared) - misses
        result.notes.append(
            f"{len(batches)} batch(es) covering {sum(len(b) for b in batches)} node(s); "
            f"{misses} would call the API, {result.cache_hits} served from cache.")
        return result

    if client is None:
        raise ValueError("enrich() needs a client unless dry_run is set")

    ledger = ledger or Ledger.open()
    ledger.check(misses)                       # refuses before any call is made

    by_id = {n.id: n for n in doc.nodes}
    for batch, key, hit in prepared:
        if hit is None:
            user = json.dumps({"components": batch}, indent=2)
            raw, finish = client.complete(SYSTEM, user)
            ledger.record(1)
            result.calls_made += 1
            try:
                hit = extract_json(raw)
            except Exception as exc:           # noqa: BLE001 - reported, not raised
                if finish == "length":
                    result.notes.append(
                        f"batch of {len(batch)} node(s) skipped: the response hit the "
                        f"token limit and arrived truncated. Re-run with --batches to "
                        f"process fewer at a time.")
                else:
                    result.notes.append(f"batch skipped: {exc}")
                result.skipped.extend(item["id"] for item in batch)
                continue
            cache.put(key, hit)
        else:
            result.cache_hits += 1

        nodes = hit.get("nodes") if isinstance(hit, dict) else None
        if not isinstance(nodes, dict):
            result.notes.append("batch skipped: response had no 'nodes' object")
            result.skipped.extend(item["id"] for item in batch)
            continue

        described = {item["id"] for item in batch}
        for node_id, payload in nodes.items():
            if node_id not in described or node_id not in by_id:
                continue                       # a node the model invented
            if not isinstance(payload, dict):
                continue
            node = by_id[node_id]
            context = _context_from(payload, node, model)
            if context is None:
                result.skipped.append(node_id)
                continue
            node.ai_context = context
            result.enriched.append(node_id)

        result.skipped.extend(sorted(described - set(nodes) - set(result.enriched)))

    if result.enriched:
        doc.version = "1.1"                    # ai_context.provenance requires 1.1
    return result
