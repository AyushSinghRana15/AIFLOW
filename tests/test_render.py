#!/usr/bin/env python3
"""Layout and renderer conformance tests."""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiflow import Document, Graph  # noqa: E402
from aiflow.cli import main as cli_main  # noqa: E402
from aiflow.layout import compute_layout  # noqa: E402
from aiflow.render import build_payload, render_html  # noqa: E402

GOLDEN_PATH = ROOT / "examples" / "rag-support-agent.aiflow"
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


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli_main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def cyclic_doc() -> Document:
    """Two agents that call each other, plus a straight-through path."""
    return Document.from_dict({
        "format": "aiflow", "version": "1.0",
        "nodes": [
            {"id": "in_x", "type": "input"},
            {"id": "a", "type": "agent"}, {"id": "b", "type": "agent"},
            {"id": "out_x", "type": "output"},
        ],
        "edges": [
            {"id": "e1", "type": "passes", "source": "in_x", "target": "a"},
            {"id": "e2", "type": "calls", "source": "a", "target": "b"},
            {"id": "e3", "type": "calls", "source": "b", "target": "a"},
            {"id": "e4", "type": "produces", "source": "b", "target": "out_x"},
        ],
    })


# ---------------------------------------------------------------------------
def suite_layout(r: Results):
    g = Graph(Document.load(GOLDEN_PATH))
    L = compute_layout(g)

    r.eq(len(L.placements), len(g.doc.nodes), "layout: every node is placed")
    r.check(L.to_dict() == compute_layout(Graph(Document.load(GOLDEN_PATH))).to_dict(),
            "layout: identical input produces identical output")

    ranks = {p.id: p.layer for p in L.placements.values()}
    violations = [e.id for e in g.doc.edges
                  if e.id not in L.back_edges and ranks[e.target] <= ranks[e.source]]
    r.eq(violations, [], "layout: every forward edge points to a later layer")

    r.eq(ranks["in_user_query"], 0, "layout: the entry point sits in layer 0")
    r.check(ranks["out_response"] == max(ranks.values()),
            "layout: the terminal sits in the last layer")

    r.check(L.width > L.height, "layout: horizontal orientation is wider than tall")
    V = compute_layout(g, orientation="vertical")
    r.check(V.height > V.width, "layout: vertical orientation is taller than wide")

    # non-overlap within a layer
    overlaps = []
    for layer in L.layers:
        ys = sorted(L.placements[n].y for n in layer)
        overlaps += [1 for a, b in zip(ys, ys[1:]) if b - a < 54]
    r.eq(overlaps, [], "layout: nodes in a layer do not overlap")

    # cycles must not hang or crash
    cg = Graph(cyclic_doc())
    CL = compute_layout(cg)
    r.eq(len(CL.placements), 4, "layout: a cyclic workflow still places every node")
    r.check(len(CL.back_edges) >= 1, "layout: the back edge of a cycle is identified")
    r.check("e3" in CL.back_edges or "e2" in CL.back_edges,
            "layout: the back edge is one of the two mutual calls", CL.back_edges)

    empty = Document.from_dict({"format": "aiflow", "version": "1.0",
                                "nodes": [], "edges": []})
    E = compute_layout(Graph(empty))
    r.eq(len(E.placements), 0, "layout: an empty document does not crash")


def suite_render(r: Results):
    doc = Document.load(GOLDEN_PATH)
    html = render_html(doc)

    # self-contained: nothing may be fetched at view time
    external = re.findall(r'(?:src|href)\s*=\s*["\'](https?:)?//[^"\']+', html)
    external = [u for u in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', html)
                if u.startswith(("http://", "https://", "//"))
                and "github.com" not in u]
    r.eq(external, [], "render: no external resources are fetched")
    r.check("@import" not in html, "render: no CSS @import")
    r.check("<script src" not in html, "render: no external script tag")

    # embedded payload must survive extraction
    m = re.search(r'<script id="aiflow-data" type="application/json">(.*?)</script>',
                  html, re.S)
    r.check(m is not None, "render: the payload is embedded and extractable")
    if m:
        payload = json.loads(m.group(1).replace("<\\/", "</"))
        r.eq(payload["document"]["nodes"][0]["id"], "in_user_query",
             "render: the embedded document survives the round trip")
        r.eq(len(payload["analysis"]["paths"]), 3, "render: analyses are embedded")

    # a literal </script> in the data must not close the tag early
    tricky = Document.load(GOLDEN_PATH)
    tricky.nodes[0].description = "</script><img src=x onerror=alert(1)>"
    thtml = render_html(tricky)
    body = thtml.split('<script id="aiflow-data" type="application/json">')[1]
    r.check(body.split("</script>")[0].count("<\\/script>") >= 1,
            "render: a </script> inside the payload is neutralised")

    # the title is interpolated into markup, so it must be escaped
    xss = Document.load(GOLDEN_PATH)
    xss.project.name = '<img src=x onerror=alert(1)>"'
    xhtml = render_html(xss)
    title = re.search(r"<title>(.*?)</title>", xhtml, re.S).group(1)
    r.check("<img" not in title and "&lt;img" in title,
            "render: the project name is escaped in the title element", title)
    # the same string also rides along in the JSON payload, where it is inert;
    # assert it appears *only* there and never in live markup
    outside = xhtml.split('<script id="aiflow-data" type="application/json">')[0] + \
        xhtml.split("</script>")[-1]
    r.check("<img src=x" not in outside,
            "render: the raw string never reaches live markup")

    evil = Document.load(GOLDEN_PATH)
    evil.nodes[0].source[0].url = "javascript:alert(1)"
    evil_payload = build_payload(evil)
    evil_ref = evil_payload["document"]["nodes"][0]["source"][0]
    r.check("_url" not in evil_ref,
            "render: a javascript: source URL never becomes a link target")
    r.check(evil_ref.get("url") == "javascript:alert(1)",
            "render: the original field is preserved in the document, just not linked")

    # source permalinks
    payload = build_payload(doc)
    node = next(n for n in payload["document"]["nodes"] if n["id"] == "retr_kb")
    r.eq(node["source"][0]["_url"],
         "https://github.com/acme/support-agent/blob/9f3c1ab/src/rag/retriever.py#L12-L58",
         "render: a source ref becomes a commit-pinned permalink")

    no_repo = Document.load(GOLDEN_PATH)
    no_repo.project.repository = None
    p2 = build_payload(no_repo)
    n2 = next(n for n in p2["document"]["nodes"] if n["id"] == "retr_kb")
    r.check("_url" not in n2["source"][0],
            "render: no permalink is invented when the repository is unknown")

    bare = Document.from_dict({"format": "aiflow", "version": "1.0",
                               "nodes": [{"id": "in_a", "type": "input"}], "edges": []})
    r.check("AIFLOW workflow" in render_html(bare),
            "render: a document with no project still renders")


def suite_render_cli(r: Results):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out = tmp / "out.html"
        code, msg, _ = run_cli("render", str(GOLDEN_PATH), "-o", str(out))
        r.eq(code, 0, "cli: render succeeds")
        r.check(out.is_file() and out.stat().st_size > 10_000, "cli: render writes a page")

        # default output path derives from the input
        copy = tmp / "wf.aiflow"
        copy.write_text(GOLDEN_PATH.read_text())
        code, _, _ = run_cli("render", str(copy))
        r.eq(code, 0, "cli: render defaults its output path")
        r.check((tmp / "wf.html").is_file(), "cli: the default output sits beside the input")

        broken = json.loads(GOLDEN_PATH.read_text())
        broken["edges"][0]["target"] = "ghost"
        bad = tmp / "bad.aiflow"
        bad.write_text(json.dumps(broken))

        code, _, err = run_cli("render", str(bad), "-o", str(tmp / "bad.html"))
        r.eq(code, 1, "cli: render refuses an invalid document")
        r.check("AF211" in err, "cli: the refusal names the diagnostic")
        r.check(not (tmp / "bad.html").exists(),
                "cli: nothing is written when render refuses")

        code, _, _ = run_cli("render", str(bad), "-o", str(tmp / "forced.html"), "--force")
        r.eq(code, 0, "cli: --force renders anyway")
        r.check((tmp / "forced.html").is_file(), "cli: --force produces output")

        code, _, _ = run_cli("render", str(GOLDEN_PATH), "-o", str(tmp / "v.html"),
                             "--orientation", "vertical")
        r.eq(code, 0, "cli: vertical orientation is accepted")


# ---------------------------------------------------------------------------
def main() -> int:
    r = Results()
    for name, suite in (("layout", suite_layout), ("render", suite_render),
                        ("render-cli", suite_render_cli)):
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
