#!/usr/bin/env python3
"""Analyzer conformance tests."""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiflow.analyze import analyze_source, generate  # noqa: E402
from aiflow.analyze.assemble import _snake  # noqa: E402
from aiflow.cli import main as cli_main  # noqa: E402
from aiflow.validate import validate  # noqa: E402

FIXTURE = ROOT / "examples" / "sample-project"
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


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli_main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def kinds(source: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in analyze_source(source, "t.py", "t").findings:
        out.setdefault(f.kind, []).append(f.name)
    return out


# ---------------------------------------------------------------------------
def suite_detection(r: Results):
    r.eq(kinds("""
import anthropic
c = anthropic.Anthropic()
def go():
    return c.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=[])
""").get("llm"), ["t.create"], "detect: an Anthropic messages.create is an LLM call")

    r.eq(kinds("""
from openai import OpenAI
c = OpenAI()
def go():
    return c.chat.completions.create(model="gpt-4o", messages=[])
""").get("llm"), ["t.create"], "detect: an OpenAI chat completion is an LLM call")

    r.check("llm" not in kinds("def create(): pass\ncreate()"),
            "detect: a bare create() is not an LLM call")

    found = kinds('SYSTEM_PROMPT = "You are a helpful assistant answering {question} carefully."')
    r.eq(found.get("prompt"), ["SYSTEM_PROMPT"], "detect: a named prompt constant")

    r.check("prompt" not in kinds('NAME = "short"'),
            "detect: a short string is not a prompt")

    shaped = kinds('BLURB = """A long block of text with a {placeholder} inside it\\nand a second line to make it look like a template."""')
    r.eq(shaped.get("prompt"), ["BLURB"], "detect: a placeholder-bearing block is a prompt")

    r.eq(kinds('MODEL = "claude-haiku-4-5-20251001"').get("model"), ["MODEL"],
         "detect: a model identifier constant")
    r.check("model" not in kinds('NAME = "claude is nice"'),
            "detect: prose mentioning a provider is not a model id")

    r.eq(kinds("""
@tool
def search(q: str) -> str:
    \"\"\"Search things.\"\"\"
    return q
""").get("tool"), ["search"], "detect: a @tool decorated function")

    r.eq(kinds("""
TOOLS = [{"name": "lookup", "description": "d", "input_schema": {"type": "object"}}]
""").get("tool"), ["lookup"], "detect: a tool schema dict literal")

    r.eq(kinds("""
import chromadb
client = chromadb.PersistentClient(path="./x")
""").get("vector_store"), ["client"], "detect: a Chroma client")

    r.eq(kinds("""
from fastapi import FastAPI
api = FastAPI()
@api.post("/ask")
def ask(q: str):
    return q
""").get("entrypoint"), ["ask"], "detect: a FastAPI route is an entrypoint")

    # an agent is inferred from evidence, never from a name
    named_only = kinds("class SupervisorAgent:\n    def go(self):\n        return 1\n")
    r.check("agent" not in named_only,
            "detect: a class is not an agent merely because of its name")

    with_call = kinds("""
import anthropic
c = anthropic.Anthropic()
class Thing:
    def go(self):
        return c.messages.create(model="claude-sonnet-4-5", messages=[])
""")
    r.eq(with_call.get("agent"), ["Thing"],
         "detect: a class containing an LLM call is an agent, whatever it is called")

    broken = analyze_source("def f(:\n  pass", "bad.py", "bad")
    r.eq(broken.findings, [], "detect: a syntax error yields no findings")
    r.check(broken.notes and "syntax error" in broken.notes[0],
            "detect: a syntax error is reported as a note")


def suite_project(r: Results):
    doc, reports = generate(FIXTURE)
    d = doc.to_dict()
    node_types = {n["id"]: n["type"] for n in d["nodes"]}

    report = validate(d)
    r.eq(len(report.errors), 0, "project: the generated document validates",
         [str(f) for f in report.errors])
    r.eq(len(report.warnings), 0, "project: the generated document has no warnings",
         [str(f) for f in report.warnings])

    r.eq(sorted(t for t in node_types.values()),
         ["agent", "agent", "input", "llm", "llm", "output", "prompt", "prompt",
          "retriever", "tool", "vector_store"],
         "project: every component of the fixture is represented")

    edges = {(e["type"], e["source"], e["target"]) for e in d["edges"]}
    r.check(("passes", "supervisor_agent", "answerer_agent") in edges,
            "project: a dataflow edge is emitted where one call consumes another's result")
    r.check(("retrieves", "kb_retriever", "vs_kb_collection") in edges,
            "project: the retriever is linked to the store it queries across files")
    r.check(("calls", "answerer_agent", "tool_lookup_ticket") in edges,
            "project: the tool is attributed to the agent that references it")
    r.check(("produces", "answerer_agent", "out_handle_support_request") in edges,
            "project: the last component before the return produces the output")

    # the Chroma client is plumbing; the collection is the store
    r.eq([n["id"] for n in d["nodes"] if n["type"] == "vector_store"], ["vs_kb_collection"],
         "project: a client superseded by its collection is not also a store")

    # ids
    r.check(all(n["id"].replace("_", "a").isalnum() for n in d["nodes"]),
            "project: node ids are well formed")
    r.check(any(n["id"] == "prompt_router_prompt" for n in d["nodes"]),
            "project: ALL_CAPS names produce readable ids",
            [n["id"] for n in d["nodes"] if n["type"] == "prompt"])

    # provenance discipline
    elements = d["nodes"] + d["edges"] + d.get("prompts", []) + d.get("models", []) \
        + d.get("tools", []) + d.get("data_sources", [])
    r.check(all(e.get("provenance", {}).get("method") == "static_analysis" for e in elements),
            "project: everything emitted claims static_analysis and nothing else")
    r.check(all(e["provenance"].get("evidence") for e in elements),
            "project: every claim cites the code that justified it")
    r.check(all(0 < e["provenance"].get("confidence", 0) <= 1 for e in elements),
            "project: every claim carries a confidence")

    # the honesty boundary: static analysis must not invent semantics
    r.check(not any("ai_context" in e for e in elements),
            "project: no ai_context is invented by the static analyzer")

    r.check(doc.project.languages == ["python"], "project: language is recorded")
    r.eq(doc.metadata["files_analyzed"], len(reports), "project: file count is recorded")
    r.check("static analysis" in (doc.provenance.notes or "").lower(),
            "project: the document states the limits of how it was produced")


def suite_snake(r: Results):
    for raw, want in [("ROUTER_PROMPT", "router_prompt"), ("KBRetriever", "kb_retriever"),
                      ("SupervisorAgent", "supervisor_agent"), ("lookup_ticket", "lookup_ticket"),
                      ("claude-sonnet-4-5", "claude_sonnet_4_5"), ("", "x")]:
        r.eq(_snake(raw), want, f"snake: {raw or '<empty>'}")


def suite_cli(r: Results):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out = tmp / "gen.aiflow"
        code, msg, _ = run_cli("generate", str(FIXTURE), "-o", str(out))
        r.eq(code, 0, "cli: generate succeeds on the fixture")
        r.check(out.is_file(), "cli: generate writes the document")
        r.check("branching is not extracted" in msg,
                "cli: generate states what static analysis could not see")

        code, _, err = run_cli("generate", str(FIXTURE), "-o", str(out))
        r.eq(code, 1, "cli: generate refuses to clobber")
        r.eq(run_cli("generate", str(FIXTURE), "-o", str(out), "--force")[0], 0,
             "cli: --force overwrites")

        r.eq(run_cli("validate", str(out), "--strict")[0], 0,
             "cli: the generated document passes strict validation")
        r.eq(run_cli("render", str(out), "-o", str(tmp / "g.html"))[0], 0,
             "cli: the generated document renders")

        code, _, err = run_cli("generate", str(tmp / "nope"))
        r.eq(code, 1, "cli: a missing path exits non-zero")

        empty = tmp / "empty"
        empty.mkdir()
        code, msg, _ = run_cli("generate", str(empty), "-o", str(tmp / "e.aiflow"))
        r.eq(code, 0, "cli: an empty project still produces a document")
        r.check("no entrypoint found" in msg,
                "cli: an empty project is reported as having no entrypoint")


# ---------------------------------------------------------------------------
def main() -> int:
    r = Results()
    for name, suite in (("detection", suite_detection), ("project", suite_project),
                        ("snake", suite_snake), ("cli", suite_cli)):
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
