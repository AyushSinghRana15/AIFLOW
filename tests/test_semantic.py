#!/usr/bin/env python3
"""Semantic analyzer tests.

Every test here runs offline against a fake client. The suite must never make a
network call or need an API key: a test suite that spends a rate-limited quota
is a test suite people stop running.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_HOME = tempfile.mkdtemp(prefix="aiflow-test-home-")
os.environ["AIFLOW_HOME"] = _TMP_HOME          # before any budget import resolves it

from aiflow import Document, validate                                   # noqa: E402
from aiflow.graph import Graph                                          # noqa: E402
from aiflow.semantic import BudgetExceeded, Cache, Ledger, enrich, plan  # noqa: E402
from aiflow.semantic.ask import answer_from_graph, ask, digest             # noqa: E402
from aiflow.semantic.client import (MissingKey, OpenRouterClient,        # noqa: E402
                                    _redact, extract_json)

GOLDEN = ROOT / "examples" / "rag-support-agent.aiflow"
RESET, RED, GREEN = "\033[0m", "\033[31m", "\033[32m"


class Results:
    def __init__(self):
        self.passed = 0
        self.failures: list[str] = []

    def check(self, ok, label, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failures.append(f"{label}{(' — ' + str(detail)) if detail else ''}")

    def eq(self, got, want, label, detail=None):
        self.check(got == want, label, detail or f"got {got!r}, want {want!r}")


class FakeClient:
    """Stands in for OpenRouter. Records calls so tests can assert on spend."""

    def __init__(self, responder=None, model="fake/model:free"):
        self.model = model
        self.calls: list[str] = []
        self.responder = responder or self._default

    def complete(self, system, user, **kw):
        self.calls.append(user)
        return self.responder(user)

    @staticmethod
    def _default(user):
        ids = [c["id"] for c in json.loads(user)["components"]]
        return json.dumps({"nodes": {
            i: {"summary": f"summary for {i}", "intent": f"intent for {i}",
                "failure_modes": [{"description": f"{i} can fail", "handled": True}],
                "invariants": [f"{i} holds"], "confidence": 0.8}
            for i in ids}}), "stop"


def bare_doc() -> Document:
    """A document with no ai_context anywhere."""
    doc = Document.load(GOLDEN)
    for n in doc.nodes:
        n.ai_context = None
    for e in doc.edges:
        e.ai_context = None
    return doc


# ---------------------------------------------------------------------------
def suite_budget(r: Results):
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AIFLOW_HOME"] = tmp
        ledger = Ledger.open(limit=5)
        r.eq(ledger.remaining(), 5, "ledger: a fresh day has the full limit")

        ledger.check(5)
        ledger.record(3)
        r.eq(ledger.used_today(), 3, "ledger: usage is counted")
        r.eq(ledger.remaining(), 2, "ledger: remaining reflects usage")

        raised = False
        try:
            ledger.check(3)
        except BudgetExceeded as exc:
            raised, message = True, str(exc)
        r.check(raised, "ledger: a call that would cross the limit is refused")
        r.check("3 call(s) needed" in message and "2 of 5" in message,
                "ledger: the refusal says what it needed and what was left")

        r.eq(Ledger.open(limit=5).used_today(), 3, "ledger: usage persists across opens")

        os.environ["AIFLOW_DAILY_LIMIT"] = "7"
        r.eq(Ledger.open().limit, 7, "ledger: AIFLOW_DAILY_LIMIT is honoured")
        del os.environ["AIFLOW_DAILY_LIMIT"]

        ledger.path.write_text("{ not json")
        r.eq(Ledger.open(limit=5).used_today(), 0,
             "ledger: a corrupt file degrades to zero rather than crashing")
    os.environ["AIFLOW_HOME"] = _TMP_HOME


def suite_cache(r: Results):
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AIFLOW_HOME"] = tmp
        cache = Cache.open()
        key = Cache.key("m", "1", {"a": 1})

        r.check(cache.get(key) is None, "cache: a miss returns None")
        cache.put(key, {"ok": True})
        r.eq(cache.get(key), {"ok": True}, "cache: a stored response is returned")

        r.check(Cache.key("m", "1", {"a": 2}) != key, "cache: payload changes the key")
        r.check(Cache.key("m2", "1", {"a": 1}) != key, "cache: model changes the key")
        r.check(Cache.key("m", "2", {"a": 1}) != key,
                "cache: prompt version changes the key, so a reworded prompt "
                "cannot reuse an old answer")

        off = Cache.open(enabled=False)
        off.put(key, {"x": 1})
        r.check(off.get(key) is None, "cache: --no-cache neither reads nor writes")
    os.environ["AIFLOW_HOME"] = _TMP_HOME


def suite_client(r: Results):
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as empty:
        # somewhere with no .env above it, or dotenv loading would find the
        # repository's own file and there would be nothing to report
        os.chdir(empty)
        raised, msg = False, ""
        try:
            OpenRouterClient.from_env()
        except MissingKey as exc:
            raised, msg = True, str(exc)
        r.check(raised, "client: a missing key is a clear error, not a crash")
        r.check("OPENROUTER_API_KEY" in msg and "never in the repository" in msg,
                "client: the error says where the key goes and where it must not")
        r.check(".env" in msg, "client: the error mentions the .env option")

        # a .env is picked up
        pathlib.Path(empty, ".env").write_text(
            "# a comment\nOPENROUTER_API_KEY=sk-or-fromfile\nAIFLOW_MODEL=x/y:free\n")
        client = OpenRouterClient.from_env()
        r.eq(client.api_key, "sk-or-fromfile", "client: a key in .env is used")
        r.eq(client.model, "x/y:free", "client: .env can set the model too")

        # the environment wins, so a stale file cannot override a CI secret
        for key in ("OPENROUTER_API_KEY", "AIFLOW_MODEL"):
            os.environ.pop(key, None)
        os.environ["OPENROUTER_API_KEY"] = "sk-or-fromenv"
        r.eq(OpenRouterClient.from_env().api_key, "sk-or-fromenv",
             "client: an already-set variable is not overridden by .env")
        for key in ("OPENROUTER_API_KEY", "AIFLOW_MODEL"):
            os.environ.pop(key, None)
    os.chdir(cwd)
    if saved:
        os.environ["OPENROUTER_API_KEY"] = saved

    r.eq(_redact("using sk-or-v1-abc123XYZ_-def now"), "using sk-or-*** now",
         "client: keys are redacted from error text")

    r.eq(extract_json('{"a": 1}'), {"a": 1}, "client: bare JSON parses")
    r.eq(extract_json('```json\n{"a": 2}\n```'), {"a": 2}, "client: fenced JSON parses")
    r.eq(extract_json('Sure! {"a": 3} done'), {"a": 3}, "client: embedded JSON parses")
    bad = False
    try:
        extract_json("no json here at all")
    except Exception:
        bad = True
    r.check(bad, "client: unparseable output raises rather than returning None")


def suite_plan(r: Results):
    doc = bare_doc()
    batches = plan(doc)
    r.check(len(batches) >= 2, "plan: nodes are split into batches")
    r.eq(sum(len(b) for b in batches), len(doc.nodes), "plan: every node is covered")
    r.check(len(batches) < len(doc.nodes),
            "plan: batching costs fewer calls than one per node",
            f"{len(batches)} batches for {len(doc.nodes)} nodes")
    r.check(all(len(b) <= 5 for b in batches), "plan: batches are bounded")

    partial = bare_doc()
    partial.nodes[0].ai_context = Document.load(GOLDEN).nodes[1].ai_context
    r.eq(sum(len(b) for b in plan(partial)), len(doc.nodes) - 1,
         "plan: nodes that already have context are skipped")
    r.eq(sum(len(b) for b in plan(partial, overwrite=True)), len(doc.nodes),
         "plan: --overwrite includes them again")

    full = Document.load(GOLDEN)
    for n in full.nodes:
        n.ai_context = n.ai_context or partial.nodes[0].ai_context
    r.eq(plan(full), [], "plan: a fully annotated document needs no calls")


def suite_enrich(r: Results):
    doc, client = bare_doc(), FakeClient()
    ledger = Ledger.open(limit=99)
    cache = Cache.open(enabled=False)
    result = enrich(doc, client=client, ledger=ledger, cache=cache)

    r.check(result.enriched, "enrich: nodes are annotated")
    r.eq(result.calls_made, result.batches, "enrich: one call per batch")
    r.eq(len(client.calls), result.batches, "enrich: no calls beyond the batches")

    node = doc.node("retr_kb")
    r.check(node.ai_context is not None, "enrich: context is attached")
    r.eq(node.ai_context.provenance.method, "ai_inference",
         "enrich: context is marked as inferred")
    r.check(node.ai_context.provenance.confidence is not None,
            "enrich: inferred context carries a confidence")
    r.eq(node.ai_context.provenance.generator["model"], client.model,
         "enrich: the model that produced it is recorded")

    # the load-bearing separation
    r.eq(node.provenance.method, "static_analysis",
         "enrich: the node's own provenance is not downgraded to inference")

    r.eq(doc.version, "1.1", "enrich: the document declares the version it now needs")
    r.eq(len(validate(doc.to_dict()).errors), 0,
         "enrich: the result validates",
         [str(f) for f in validate(doc.to_dict()).errors])

    # the model must not be able to assert error handling it cannot see
    r.check(all(fm.handled is None
                for n in doc.nodes if n.ai_context and n.ai_context.failure_modes
                for fm in n.ai_context.failure_modes),
            "enrich: a model-supplied 'handled' flag is discarded")
    findings = Graph(doc).unhandled_failures()
    r.check(findings and all(f.inferred for f in findings),
            "enrich: failures resting on inferred context are marked inferred")

    # existing context survives
    keep = bare_doc()
    original = Document.load(GOLDEN).nodes[1].ai_context
    keep.nodes[1].ai_context = original
    enrich(keep, client=FakeClient(), ledger=ledger, cache=Cache.open(enabled=False))
    r.eq(keep.nodes[1].ai_context.summary, original.summary,
         "enrich: existing context is not overwritten by default")


def suite_spend(r: Results):
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AIFLOW_HOME"] = tmp

        doc, client = bare_doc(), FakeClient()
        result = enrich(doc, dry_run=True)
        r.eq(result.calls_made, 0, "spend: a dry run makes no calls")
        r.check(result.batches > 0, "spend: a dry run still reports the cost")

        cache = Cache.open()
        first = enrich(bare_doc(), client=client, ledger=Ledger.open(limit=99), cache=cache)
        before = len(client.calls)
        second = enrich(bare_doc(), client=client, ledger=Ledger.open(limit=99), cache=cache)
        r.eq(len(client.calls), before, "spend: an unchanged document re-runs for free")
        r.eq(second.calls_made, 0, "spend: the second run reports zero calls")
        r.eq(second.cache_hits, first.batches, "spend: every batch came from cache")
        r.check(second.enriched, "spend: a cached run still annotates the document")

        tight = Ledger.open(limit=1)
        tight.record(1)
        starved = FakeClient()
        raised = False
        try:
            enrich(bare_doc(), client=starved, ledger=tight, cache=Cache.open(enabled=False))
        except BudgetExceeded:
            raised = True
        r.check(raised, "spend: the run is refused when it would exceed the budget")
        r.eq(len(starved.calls), 0,
             "spend: the refusal happens before any call, not after some")
    os.environ["AIFLOW_HOME"] = _TMP_HOME


def suite_robustness(r: Results):
    ledger, cache = Ledger.open(limit=99), Cache.open(enabled=False)

    truncated = FakeClient(lambda user: ('{"nodes": {"retr_kb": {"summary": "cut off', "length"))
    result = enrich(bare_doc(), client=truncated, ledger=ledger, cache=cache)
    r.check(any("token limit" in n for n in result.notes),
            "robust: a truncated response is reported as truncation", result.notes)
    r.check(not result.enriched, "robust: nothing is written from a truncated batch")

    prose = FakeClient(lambda user: ("I'm afraid I can't do that.", "stop"))
    result = enrich(bare_doc(), client=prose, ledger=ledger, cache=cache)
    r.check(result.notes and not result.enriched,
            "robust: a non-JSON response is skipped, not crashed on")

    def invents(user):
        return json.dumps({"nodes": {"a_node_that_does_not_exist": {
            "summary": "invented", "confidence": 0.9}}}), "stop"
    doc = bare_doc()
    enrich(doc, client=FakeClient(invents), ledger=ledger, cache=cache)
    r.check(all(n.ai_context is None for n in doc.nodes),
            "robust: a node the model invented is discarded")

    def empty(user):
        ids = [c["id"] for c in json.loads(user)["components"]]
        return json.dumps({"nodes": {i: {"confidence": 0.9} for i in ids}}), "stop"
    doc = bare_doc()
    result = enrich(doc, client=FakeClient(empty), ledger=ledger, cache=cache)
    r.check(all(n.ai_context is None for n in doc.nodes),
            "robust: a response with no actual content adds no empty context")
    r.check(result.skipped, "robust: those nodes are reported as skipped")

    def unstated(user):
        ids = [c["id"] for c in json.loads(user)["components"]]
        return json.dumps({"nodes": {i: {"summary": "x"} for i in ids}}), "stop"
    doc = bare_doc()
    enrich(doc, client=FakeClient(unstated), ledger=ledger, cache=cache)
    conf = doc.nodes[0].ai_context.provenance.confidence
    r.check(conf is not None and conf <= 0.5,
            "robust: unstated confidence is not treated as high confidence", conf)
    r.eq(len(validate(doc.to_dict()).errors), 0,
         "robust: even a thin response yields a valid document")


def suite_ask_graph(r: Results):
    """Questions the graph can answer exactly must never reach a model."""
    doc = Document.load(GOLDEN)

    cases = [
        ("show me all paths to the final response", "paths"),
        ("where is RAG used?", "rag"),
        ("which agents use external tools?", "tools"),
        ("find missing error handling", "failures"),
        ("which claims were inferred?", "trust"),
        ("what happens if vs_kb fails?", "impact"),
        ("explain the retr_kb component", "describe"),
        ("give me a summary of this workflow", "summary"),
    ]
    for question, expected in cases:
        a = answer_from_graph(doc, question)
        r.check(a is not None, f"ask: {expected} question is recognised", question)
        if a:
            r.eq(a.matched, expected, f"ask: routed to the {expected} query")
            r.eq(a.calls_made, 0, f"ask: the {expected} answer costs no call")
            r.eq(a.source, "graph", f"ask: the {expected} answer is labelled computed")
            r.check(a.text.strip(), f"ask: the {expected} answer is not empty")

    a = answer_from_graph(doc, "what happens if vs_kb fails?")
    r.check("out_response" in a.text,
            "ask: an impact answer names the output the failure reaches")
    r.check("vs_kb" in a.cites, "ask: an impact answer cites the component")

    r.check(answer_from_graph(doc, "explain the Knowledge-base retriever") is not None,
            "ask: a component can be named rather than given by id")
    r.check(answer_from_graph(doc, "what happens if ghost_component fails?") is None,
            "ask: an unknown component falls through rather than answering wrongly")
    r.check(answer_from_graph(doc, "would this be cheaper on a different provider?") is None,
            "ask: an open question falls through to the model path")

    a = ask(doc, "is this workflow well designed?", allow_model=False)
    r.check(not a.grounded and "does not match" in a.text,
            "ask: --no-model says so rather than guessing")
    r.eq(a.calls_made, 0, "ask: --no-model costs nothing")


def suite_ask_model(r: Results):
    doc = Document.load(GOLDEN)

    def respond(_user):
        return json.dumps({
            "answer": "Because the router is cheap.",
            "cites": ["agent_supervisor", "not_a_real_id"],
            "confidence": 1.7, "grounded": True}), "stop"

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AIFLOW_HOME"] = tmp
        client = FakeClient(respond)
        cache = Cache.open()
        question = "why does this design use two different models?"

        a = ask(doc, question, client=client, ledger=Ledger.open(limit=9), cache=cache)
        r.eq(a.calls_made, 1, "ask: an open question costs one call")
        r.eq(a.source, client.model, "ask: the answer is attributed to the model")
        r.eq(a.cites, ["agent_supervisor"],
             "ask: a citation to something not in the document is dropped")
        r.check(a.confidence is not None and a.confidence <= 1.0,
                "ask: confidence is clamped to the valid range", a.confidence)

        before = len(client.calls)
        again = ask(doc, question, client=client, ledger=Ledger.open(limit=9), cache=cache)
        r.eq(len(client.calls), before, "ask: the same question re-runs from cache")
        r.eq(again.calls_made, 0, "ask: and reports zero calls")

        ungrounded = FakeClient(lambda u: (json.dumps(
            {"answer": "The document does not say.", "grounded": False,
             "confidence": 0.9}), "stop"))
        a = ask(doc, "what is the p99 latency?", client=ungrounded,
                ledger=Ledger.open(limit=9), cache=Cache.open(enabled=False))
        r.check(not a.grounded,
                "ask: an ungrounded answer is flagged rather than presented as fact")

        truncated = FakeClient(lambda u: ('{"answer": "cut', "length"))
        a = ask(doc, "explain everything in detail please", client=truncated,
                ledger=Ledger.open(limit=9), cache=Cache.open(enabled=False))
        r.check("truncated" in a.text and not a.grounded,
                "ask: a truncated reply is reported as truncation")

        tight = Ledger.open(limit=1)
        tight.record(1)
        starved = FakeClient()
        raised = False
        try:
            ask(doc, "an entirely novel open question about tradeoffs", client=starved,
                ledger=tight, cache=Cache.open(enabled=False))
        except BudgetExceeded:
            raised = True
        r.check(raised, "ask: the budget is enforced on the model path too")
        r.eq(len(starved.calls), 0, "ask: and refused before the call")
    os.environ["AIFLOW_HOME"] = _TMP_HOME

    d = digest(doc)
    r.eq(len(d["nodes"]), len(doc.nodes), "digest: every node is described")
    r.eq(len(d["edges"]), len(doc.edges), "digest: every edge is described")
    r.check(len(json.dumps(d)) < 20000, "digest: the payload stays bounded")
    inferred = [n for n in d["nodes"] if n.get("context", {}).get("_note")]
    r.check(all("ai_inference" in n["context"]["_note"] for n in inferred),
            "digest: inferred context is marked as such for the model")


# ---------------------------------------------------------------------------
def main() -> int:
    r = Results()
    for name, suite in (("budget", suite_budget), ("cache", suite_cache),
                        ("client", suite_client), ("plan", suite_plan),
                        ("enrich", suite_enrich), ("spend", suite_spend),
                        ("robustness", suite_robustness),
                        ("ask-graph", suite_ask_graph), ("ask-model", suite_ask_model)):
        print(f"\n{name}")
        suite(r)

    total = r.passed + len(r.failures)
    print()
    if r.failures:
        for f in r.failures:
            print(f"  {RED}FAIL{RESET} {f}")
        print(f"\n{RED}{len(r.failures)} failed{RESET}, {r.passed} passed, {total} total")
        return 1
    print(f"{GREEN}all {total} assertions passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
