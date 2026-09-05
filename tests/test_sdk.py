#!/usr/bin/env python3
"""SDK conformance tests: model round-tripping, graph queries, diff, CLI."""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aiflow  # noqa: E402
from aiflow import Document, Graph, diff  # noqa: E402
from aiflow.cli import main as cli_main  # noqa: E402

GOLDEN_PATH = ROOT / "examples" / "rag-support-agent.aiflow"
GOLDEN_RAW = json.loads(GOLDEN_PATH.read_text())

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

    def eq(self, got, want, label):
        self.check(got == want, label, f"got {got!r}, want {want!r}")


def run_cli(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli_main(list(argv))
        except SystemExit as exc:          # argparse errors
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
def suite_model(r: Results):
    doc = Document.from_dict(GOLDEN_RAW)

    r.check(doc.to_dict() == GOLDEN_RAW,
            "model: decode/encode is lossless on the reference document")

    # absent vs present-but-empty must stay distinguishable, or diff reports phantoms
    with_empty = json.loads(json.dumps(GOLDEN_RAW))
    with_empty["nodes"][0]["tags"] = []
    r.check(Document.from_dict(with_empty).to_dict() == with_empty,
            "model: an explicitly empty list survives the round trip")

    absent = json.loads(json.dumps(GOLDEN_RAW))
    r.check("tags" not in Document.from_dict(absent).to_dict()["nodes"][0],
            "model: an absent field is not materialised on encode")

    # file round trip
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rt.aiflow"
        doc.save(path)
        r.check(json.loads(path.read_text()) == GOLDEN_RAW,
                "model: save/load round trip is byte-stable in content")

    r.eq(doc.node("retr_kb").type, "retriever", "model: node lookup by id")
    r.eq(doc.resolve(doc.node("llm_answer")).model, "claude-sonnet-4-5",
         "model: ref resolves to the right registry entry")
    r.check(doc.resolve(doc.node("agent_supervisor")) is None,
            "model: a node type that takes no ref resolves to None")
    r.eq(str(doc.node("retr_kb").source[0]), "rag/retriever.py:12-58",
         "model: source ref renders as a code location")

    prov = doc.edge("e_ticket_to_answerer").provenance
    r.check(prov.is_inferred, "model: ai_inference is recognised as inferred")
    r.check(not prov.is_trusted, "model: an unreviewed inference is not trusted")
    r.check(doc.node("retr_kb").provenance.is_trusted,
            "model: a statically-analysed claim is trusted")


def suite_graph(r: Results):
    doc = Document.load(GOLDEN_PATH)
    g = Graph(doc)

    r.eq([n.id for n in g.entry_points()], ["in_user_query"], "graph: entry points")
    r.eq([n.id for n in g.terminals()], ["out_response"], "graph: terminals")

    paths = g.paths_to_outputs()
    r.eq(len(paths), 3, "graph: one path per branch of the condition")
    r.check(all(p.nodes[0] == "in_user_query" and p.nodes[-1] == "out_response"
                for p in paths),
            "graph: every path runs input to output")
    r.check(len({p.nodes for p in paths}) == 3, "graph: paths are distinct")

    r.eq(sorted(n.id for n in g.rag_components()), ["retr_kb", "vs_kb"],
         "graph: retrieval surface")

    tools = {k: sorted(t.id for t in v) for k, v in g.agents_using_tools().items()}
    r.eq(tools, {"agent_supervisor": ["tool_ticket_lookup"]},
         "graph: tool is attributed to its nearest controlling agent")

    impact = {n.id for n in g.impact_of("vs_kb")}
    r.check("retr_kb" in impact and "out_response" in impact,
            "graph: store failure propagates through its retriever to the output",
            sorted(impact))
    r.check("vs_kb" not in impact, "graph: impact excludes the node itself")

    unhandled = {f.subject for f in g.unhandled_failures()}
    r.eq(sorted(unhandled), ["agent_supervisor", "retr_kb"],
         "graph: unhandled failure modes are surfaced")
    r.check(all("Ticket API returns 404" not in f.detail for f in g.unhandled_failures()),
            "graph: a handled failure mode is not reported")

    r.eq(g.low_trust(), [], "graph: reference document has no low-trust claims")
    r.eq([f.subject for f in g.low_trust(threshold=0.9)], ["edge:e_ticket_to_answerer"],
         "graph: raising the threshold surfaces the inferred edge")

    r.eq(g.unreachable(), [], "graph: reference document has no unreachable nodes")
    r.eq(g.summary()["inferred_elements"], 1, "graph: summary counts inferred elements")

    r.eq([e.id for e in g.out_edges("cond_route", "routes_to")],
         ["e_route_kb", "e_route_ticket", "e_route_direct"],
         "graph: edge filtering by type")


def suite_diff(r: Results):
    base = Document.load(GOLDEN_PATH)

    r.check(not diff(base, Document.load(GOLDEN_PATH)),
            "diff: a document does not differ from itself")

    # the registry payoff: p_answer is referenced by a node, but editing the
    # definition is one change, not one per reference
    edited = Document.load(GOLDEN_PATH)
    edited.prompts[1].template = "rewritten"
    result = diff(base, edited)
    r.eq(len(result.changes), 1, "diff: editing a shared prompt reports exactly one change")
    r.eq(result.changes[0].kind, "prompts", "diff: the change is attributed to the registry")

    added = Document.load(GOLDEN_PATH)
    added.nodes.append(aiflow.Node(id="n_new", type="agent"))
    r.eq([(c.op, c.id) for c in diff(base, added).changes], [("added", "n_new")],
         "diff: an added node is reported once")

    removed = Document.load(GOLDEN_PATH)
    removed.nodes = [n for n in removed.nodes if n.id != "prompt_router"]
    r.eq([(c.op, c.id) for c in diff(base, removed).changes],
         [("removed", "prompt_router")], "diff: a removed node is reported once")

    reordered = Document.load(GOLDEN_PATH)
    reordered.nodes.reverse()
    reordered.edges.reverse()
    r.check(not diff(base, reordered),
            "diff: reordering arrays is not a semantic change")

    retargeted = Document.load(GOLDEN_PATH)
    retargeted.data_sources[0].config["namespace"] = "kb-v4"
    changes = diff(base, retargeted).changes
    r.eq(len(changes), 1, "diff: retargeting an index is a single-site change")
    r.eq(changes[0].field, "config", "diff: reports the field that moved")


def suite_cli(r: Results):
    code, out, _ = run_cli("validate", str(GOLDEN_PATH), "--strict")
    r.eq(code, 0, "cli: validate accepts the reference document")
    r.check("OK" in out, "cli: validate reports OK")

    code, out, _ = run_cli("validate", str(GOLDEN_PATH), "--json")
    r.check(json.loads(out)["ok"] is True, "cli: validate --json emits a machine-readable verdict")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        broken = json.loads(json.dumps(GOLDEN_RAW))
        broken["edges"][0]["target"] = "ghost"
        bad = tmp / "bad.aiflow"
        bad.write_text(json.dumps(broken))
        code, out, _ = run_cli("validate", str(bad))
        r.eq(code, 1, "cli: validate fails on a dangling edge")
        r.check("AF211" in out, "cli: the failure carries its diagnostic code")

        notjson = tmp / "bad.json"
        notjson.write_text("{not json")
        code, _, err = run_cli("validate", str(notjson))
        r.eq(code, 1, "cli: malformed JSON exits non-zero")
        r.check("not valid JSON" in err, "cli: malformed JSON explains itself")

        code, _, err = run_cli("validate", str(tmp / "missing.aiflow"))
        r.eq(code, 1, "cli: a missing file exits non-zero")

        skeleton = tmp / "project.aiflow"
        code, _, _ = run_cli("init", "-o", str(skeleton), "--name", "demo")
        r.eq(code, 0, "cli: init succeeds")
        r.eq(run_cli("validate", str(skeleton), "--strict")[0], 0,
             "cli: the generated skeleton is strictly valid")

        code, _, err = run_cli("init", "-o", str(skeleton))
        r.eq(code, 1, "cli: init refuses to clobber an existing file")
        r.eq(run_cli("init", "-o", str(skeleton), "--force")[0], 0,
             "cli: --force overwrites")

        code, out, _ = run_cli("diff", str(GOLDEN_PATH), str(GOLDEN_PATH), "--exit-code")
        r.eq(code, 0, "cli: diff of identical documents exits 0")

        code, out, _ = run_cli("diff", str(GOLDEN_PATH), str(skeleton), "--exit-code")
        r.eq(code, 1, "cli: --exit-code signals a difference")

    code, out, _ = run_cli("inspect", str(GOLDEN_PATH), "--json", "--paths", "--rag",
                           "--tools", "--unhandled", "--inferred")
    payload = json.loads(out)
    r.eq(code, 0, "cli: inspect succeeds")
    r.eq(len(payload["paths"]), 3, "cli: inspect --paths matches the graph API")
    r.eq(payload["rag"], ["retr_kb", "vs_kb"], "cli: inspect --rag")
    r.eq(len(payload["unhandled_failures"]), 2, "cli: inspect --unhandled")

    code, _, err = run_cli("inspect", str(GOLDEN_PATH), "--node", "nope")
    r.eq(code, 1, "cli: inspect of an unknown node exits non-zero")

    code, out, _ = run_cli("inspect", str(GOLDEN_PATH), "--node", "retr_kb")
    r.eq(code, 0, "cli: inspect of a known node succeeds")
    r.check("UNHANDLED" in out or "unhandled" in out.lower(),
            "cli: node detail flags its unhandled failure")

    for cmd in ("generate", "render"):
        code, _, err = run_cli(cmd, ".")
        r.eq(code, 2, f"cli: {cmd} exits 2 rather than pretending to work")
        r.check("not implemented" in err, f"cli: {cmd} says so plainly")


def suite_spec(r: Results):
    r.eq(aiflow.schema()["title"], "AIFLOW v1", "spec: schema resolves")
    r.eq(len(aiflow.node_types()), 9, "spec: nine node types")
    r.eq(len(aiflow.edge_types()), 6, "spec: six edge types")
    r.check(all(name in aiflow.__all__ for name in ("Document", "Graph", "diff", "validate")),
            "spec: the public surface is exported")


# ---------------------------------------------------------------------------
def main() -> int:
    r = Results()
    for name, suite in (("model", suite_model), ("graph", suite_graph),
                        ("diff", suite_diff), ("cli", suite_cli), ("spec", suite_spec)):
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
