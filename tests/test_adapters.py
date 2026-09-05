#!/usr/bin/env python3
"""Framework adapter tests.

The load-bearing claim of the adapter layer is that it extracts what generic
analysis refuses to guess -- branching -- and labels it as a firmer claim.
Both halves are asserted here.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiflow import validate                                    # noqa: E402
from aiflow.adapters import ADAPTERS, detect, run              # noqa: E402
from aiflow.adapters.crewai import CrewAIAdapter                # noqa: E402
from aiflow.adapters.langchain import LangChainAdapter          # noqa: E402
from aiflow.adapters.langgraph import LangGraphAdapter          # noqa: E402
from aiflow.adapters.llamaindex import LlamaIndexAdapter        # noqa: E402
from aiflow.adapters.openai_agents import OpenAIAgentsAdapter   # noqa: E402
from aiflow.analyze import generate                            # noqa: E402
from aiflow.analyze.python import analyze_path, analyze_source  # noqa: E402
from aiflow.graph import Graph                                 # noqa: E402

LANGGRAPH = ROOT / "examples" / "langgraph-project"
PLAIN = ROOT / "examples" / "sample-project"

FIXTURES = {
    "langgraph": ROOT / "examples" / "langgraph-project",
    "langchain": ROOT / "examples" / "langchain-project",
    "openai-agents": ROOT / "examples" / "openai-agents-project",
    "crewai": ROOT / "examples" / "crewai-project",
    "llamaindex": ROOT / "examples" / "llamaindex-project",
}
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


def walk(source: str, adapter=None):
    """Run one adapter over one snippet."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "g.py"
        path.write_text(source)
        reports = analyze_path(Path(tmp))
        return (adapter or LangGraphAdapter()).analyze(Path(tmp), reports)


def kinds(result) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in result.findings:
        out.setdefault(f.kind, []).append(f.name)
    return out


HEADER = "from langgraph.graph import END, START, StateGraph\nb = StateGraph(dict)\n"


# ---------------------------------------------------------------------------
def suite_detection(r: Results):
    r.check(any(a.framework == "langgraph" for a in ADAPTERS),
            "registry: the LangGraph adapter is registered")

    r.eq([a.framework for a in detect(analyze_path(LANGGRAPH))], ["langgraph"],
         "detect: LangGraph is detected from its import")
    r.eq(detect(analyze_path(PLAIN)), [],
         "detect: a project without the framework gets no adapter")

    r.eq(analyze_source("import langgraph\n", "x.py", "x").frameworks,
         {"langgraph": "langgraph"}, "detect: the import maps to the framework key")


def suite_constructs(r: Results):
    found = kinds(walk(HEADER + 'b.add_node("a", handler)\n'))
    r.eq(found.get("fw_node"), ["a"], "construct: add_node")

    found = kinds(walk(HEADER + 'b.add_node("a", h)\nb.add_node("z", h2)\nb.add_edge("a", "z")\n'))
    r.eq(found.get("fw_edge"), ["a->z"], "construct: add_edge")

    found = kinds(walk(HEADER + 'b.add_node("a", h)\nb.add_edge(START, "a")\n'))
    r.eq(found.get("fw_entry"), ["a"], "construct: add_edge from START is an entry")

    found = kinds(walk(HEADER + 'b.add_node("z", h)\nb.add_edge("z", END)\n'))
    r.eq(found.get("fw_terminal"), ["z"], "construct: add_edge to END is a terminal")

    found = kinds(walk(HEADER + 'b.add_node("a", h)\nb.set_entry_point("a")\nb.set_finish_point("a")\n'))
    r.eq(found.get("fw_entry"), ["a"], "construct: set_entry_point")
    r.eq(found.get("fw_terminal"), ["a"], "construct: set_finish_point")

    result = walk(HEADER + 'b.add_conditional_edges("a", router, {"x": "p", "y": "q"})\n')
    branch = next(f for f in result.findings if f.kind == "fw_branch")
    r.eq(branch.data["mapping"], {"x": "p", "y": "q"}, "construct: branch mapping from a dict")
    r.eq(branch.data["router"], "router", "construct: the router function is recorded")
    r.check(not branch.data["partial"], "construct: a fully literal branch is not partial")

    result = walk(HEADER + 'b.add_conditional_edges("a", router, ["p", "q"])\n')
    branch = next(f for f in result.findings if f.kind == "fw_branch")
    r.eq(branch.data["mapping"], {"p": "p", "q": "q"},
         "construct: a list mapping means label equals destination")

    result = walk(HEADER + 'b.add_conditional_edges("a", path=router, path_map={"x": "p"})\n')
    branch = next(f for f in result.findings if f.kind == "fw_branch")
    r.eq(branch.data["router"], "router", "construct: keyword form of the router")
    r.eq(branch.data["mapping"], {"x": "p"}, "construct: keyword form of the mapping")

    r.check(not walk("b = 1\nb.add_node('a', h)\n").findings,
            "construct: method calls on a non-builder are ignored")
    r.check(kinds(walk(HEADER)).get("fw_graph") == ["b"],
            "construct: the builder itself is recorded")


def suite_honesty(r: Results):
    """What the adapter cannot see, it reports rather than silently dropping."""
    result = walk(HEADER + "b.add_node(name, handler)\n")
    r.check(not [f for f in result.findings if f.kind == "fw_node"],
            "honesty: a non-literal node name yields no step",
            [f.kind for f in result.findings])
    r.check(any("non-literal name" in n for n in result.notes),
            "honesty: and it is reported as skipped", result.notes)

    result = walk(HEADER + 'b.add_conditional_edges("a", router, ROUTES)\n')
    branch = next(f for f in result.findings if f.kind == "fw_branch")
    r.check(branch.data["partial"], "honesty: a variable mapping is marked partial")
    r.check(branch.confidence <= 0.6,
            "honesty: an unresolvable branch carries lower confidence", branch.confidence)
    r.check(any("not all literal" in n for n in result.notes),
            "honesty: and the missing routes are reported")

    result = walk(HEADER + 'b.add_edge(first, "z")\n')
    r.check(any("non-literal endpoint" in n for n in result.notes),
            "honesty: a non-literal edge endpoint is reported")


def suite_document(r: Results):
    doc, _ = generate(LANGGRAPH)
    d = doc.to_dict()
    report = validate(d)
    r.eq(len(report.errors), 0, "document: the LangGraph project validates",
         [str(f) for f in report.errors])
    r.eq(len(report.warnings), 0, "document: with no warnings",
         [str(f) for f in report.warnings])

    types = {n["id"]: n["type"] for n in d["nodes"]}
    r.check("condition" in types.values(),
            "document: a condition node exists — the thing generic analysis cannot produce")
    r.eq(types.get("retrieve"), "retriever",
         "document: a step that only retrieves is typed as a retriever")
    r.eq(types.get("classify"), "agent",
         "document: a step that calls a model is typed as an agent")

    routes = [e for e in d["edges"] if e["type"] == "routes_to"]
    r.eq(len(routes), 3, "document: one routes_to per branch target")
    r.check(all(e.get("when") for e in routes),
            "document: every branch carries its predicate")
    r.eq(sorted(e["label"] for e in routes), ["direct", "knowledge_base", "ticket"],
         "document: branch labels come from the mapping keys")

    # the provenance distinction is the point of having adapters at all
    by_id = {n["id"]: n for n in d["nodes"]}
    condition = next(n for n in d["nodes"] if n["type"] == "condition")
    r.eq(condition["provenance"]["method"], "framework_adapter",
         "document: adapter-derived structure claims framework_adapter")
    r.eq(routes[0]["provenance"]["method"], "framework_adapter",
         "document: so do the branch edges")
    r.eq(by_id["llm_classify"]["provenance"]["method"], "static_analysis",
         "document: generically-found components still claim static_analysis")

    framework = next(f for f in d["project"]["frameworks"] if f["name"] == "langgraph")
    r.check(framework.get("adapter"), "document: the framework records its adapter")

    g = Graph(doc)
    r.eq(len(g.paths_to_outputs()), 3, "document: one path per branch")
    r.eq(g.unreachable(), [], "document: nothing is orphaned")


def suite_every_adapter(r: Results):
    """Each registered adapter must be detected, apply, and produce a clean document."""
    registered = {a.framework for a in ADAPTERS}
    r.eq(registered, set(FIXTURES),
         "registry: every registered adapter has a fixture, and vice versa")

    for framework, fixture in FIXTURES.items():
        reports = analyze_path(fixture)
        chosen = [a.framework for a in detect(reports)]
        r.eq(chosen, [framework], f"{framework}: detected from its own fixture")

        doc, _ = generate(fixture)
        d = doc.to_dict()
        report = validate(d)
        r.eq(len(report.errors), 0, f"{framework}: the document validates",
             [str(f) for f in report.errors])
        r.eq(len(report.warnings), 0, f"{framework}: with no warnings",
             [str(f) for f in report.warnings])

        g = Graph(doc)
        r.check(g.paths_to_outputs(),
                f"{framework}: at least one path runs input to output")
        r.eq(g.unreachable(), [], f"{framework}: nothing is orphaned")

        adapter_claimed = [n for n in d["nodes"]
                           if n.get("provenance", {}).get("method") == "framework_adapter"]
        r.check(adapter_claimed,
                f"{framework}: the adapter's own structure claims framework_adapter")

        entry = next(f for f in d["project"]["frameworks"] if f["name"] == framework)
        r.check(entry.get("adapter"), f"{framework}: the framework records its adapter")


def suite_openai_agents(r: Results):
    a = OpenAIAgentsAdapter()
    src = ("import agents\nfrom agents import Agent, Runner\n"
           "kb = Agent(name='KB', model='gpt-4o-mini', tools=[search])\n"
           "triage = Agent(name='Triage', handoffs=[kb])\n"
           "Runner.run(triage, q)\n")
    found = kinds(walk(src, a))
    r.eq(sorted(found.get("fw_node", [])), ["kb", "triage"],
         "openai-agents: each Agent is a step")
    r.eq(found.get("fw_edge"), ["triage->kb"], "openai-agents: a handoff is an edge")
    r.eq(found.get("fw_entry"), ["triage"],
         "openai-agents: Runner.run names the entry point")
    r.eq(found.get("fw_terminal"), ["kb"],
         "openai-agents: an agent nothing hands off from is a terminal")

    result = walk("import agents\nfrom agents import Agent\n"
                  "a = Agent(name='A', handoffs=TARGETS)\n", a)
    r.check(any("not a literal list" in n for n in result.notes),
            "openai-agents: runtime handoffs are reported, not invented", result.notes)

    doc, _ = generate(FIXTURES["openai-agents"])
    tools = {k: sorted(t.id for t in v) for k, v in Graph(doc).agents_using_tools().items()}
    r.eq(tools, {"kb_agent": ["tool_search_kb"], "ticket_agent": ["tool_lookup_ticket"]},
         "openai-agents: tools declared on an agent are bound to it")


def suite_crewai(r: Results):
    a = CrewAIAdapter()
    src = ("import crewai\nfrom crewai import Agent, Crew, Process, Task\n"
           "w = Agent(role='writer', llm='gpt-4o')\n"
           "t1 = Task(description='a', agent=w)\n"
           "t2 = Task(description='b', agent=w, context=[t1])\n"
           "c = Crew(agents=[w], tasks=[t1, t2], process=Process.sequential)\n")
    found = kinds(walk(src, a))
    r.eq(sorted(found.get("fw_node", [])), ["t1", "t2"],
         "crewai: tasks are the steps, not agents")
    r.eq(found.get("fw_edge"), ["t1->t2"], "crewai: context= is a dependency edge")
    r.eq(found.get("fw_entry"), ["t1"], "crewai: the first task is the entry")
    r.eq(found.get("fw_terminal"), ["t2"], "crewai: the last task is the terminal")

    # context= already stated the ordering; the sequence must not duplicate it
    edges = [f for f in walk(src, a).findings if f.kind == "fw_edge"]
    r.eq(len(edges), 1, "crewai: a sequential edge is not duplicated by context=")

    result = walk(src.replace("Process.sequential", "Process.hierarchical"), a)
    r.check(any("hierarchical" in n for n in result.notes),
            "crewai: hierarchical ordering is reported as not extractable")
    r.check(not [f for f in result.findings if f.kind == "fw_entry"],
            "crewai: and no sequence is invented for it")


def suite_llamaindex(r: Results):
    a = LlamaIndexAdapter()
    src = ("import llama_index\nfrom llama_index.core.query_pipeline import QueryPipeline\n"
           "p = QueryPipeline()\n"
           "p.add_modules({'input': InputComponent(), 'llm': llm})\n"
           "p.add_link('input', 'llm')\n")
    found = kinds(walk(src, a))
    r.eq(sorted(found.get("fw_node", [])), ["input", "llm"],
         "llamaindex: add_modules declares the steps")
    r.eq(found.get("fw_edge"), ["input->llm"], "llamaindex: add_link declares the edges")
    r.eq(found.get("fw_terminal"), ["llm"], "llamaindex: a sink module is a terminal")
    r.check("input" not in (found.get("fw_entry") or []),
            "llamaindex: an InputComponent is the entry, not something an entry points at")

    result = walk(src.replace("{'input': InputComponent(), 'llm': llm}", "MODULES"), a)
    r.check(any("non-literal mapping" in n for n in result.notes),
            "llamaindex: a runtime module mapping is reported")

    doc, _ = generate(FIXTURES["llamaindex"])
    types = {n.id: n.type for n in doc.nodes}
    r.eq(types.get("retriever"), "retriever",
         "llamaindex: a retriever module is typed as one")
    r.check(any(e.type == "retrieves" for e in doc.edges),
            "llamaindex: the retriever is linked to the store it reads")


def suite_langchain(r: Results):
    a = LangChainAdapter()
    src = ("import langchain_core\n"
           "from langchain_core.runnables import RunnableBranch\n"
           "billing = p | m | StrOutputParser()\n"
           "tech = p | m | StrOutputParser()\n"
           "root = RunnableBranch((lambda x: x['t'] == 'billing', billing), tech)\n")
    result = walk(src, a)
    found = kinds(result)
    r.eq(sorted(found.get("fw_node", [])), ["billing", "tech"],
         "langchain: a named LCEL chain is one step, not one per pipe stage")
    branch = next(f for f in result.findings if f.kind == "fw_branch")
    r.eq(sorted(branch.data["mapping"].values()), ["billing", "tech"],
         "langchain: RunnableBranch targets become routes")
    r.check(any("billing" in k for k in branch.data["mapping"]),
            "langchain: the predicate text is kept as the route label",
            list(branch.data["mapping"]))

    # without a branch there is no declared ordering, so no graph is claimed
    plain = walk("import langchain_core\nc = p | m\n", a)
    r.eq(plain.findings, [],
         "langchain: chains with no declared ordering produce no topology")
    r.check(any("not declared in source" in n for n in plain.notes),
            "langchain: and that is stated rather than left silent")

    doc, _ = generate(FIXTURES["langchain"])
    conditions = [n for n in doc.nodes if n.type == "condition"]
    r.eq(len(conditions), 1, "langchain: the branch becomes a condition node")
    r.check(any(n.type == "input" for n in doc.nodes),
            "langchain: the generic entrypoint supplies the start the adapter cannot")


def suite_isolation(r: Results):
    """A project with no framework must be unaffected by the adapter layer."""
    with_adapters, _ = generate(PLAIN)
    without, _ = generate(PLAIN, use_adapters=False)
    r.eq(with_adapters.to_dict(), without.to_dict(),
         "isolation: adapters change nothing for a project that uses no framework")

    r.check(not any(n.type == "condition" for n in with_adapters.nodes),
            "isolation: no condition is invented where no framework declares one")
    r.check(all(f.adapter is None for f in (with_adapters.project.frameworks or [])),
            "isolation: no adapter is claimed")


# ---------------------------------------------------------------------------
def main() -> int:
    r = Results()
    for name, suite in (("detection", suite_detection), ("constructs", suite_constructs),
                        ("honesty", suite_honesty), ("document", suite_document),
                        ("every-adapter", suite_every_adapter),
                        ("openai-agents", suite_openai_agents),
                        ("crewai", suite_crewai), ("llamaindex", suite_llamaindex),
                        ("langchain", suite_langchain),
                        ("isolation", suite_isolation)):
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
