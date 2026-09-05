# AIFLOW

**A framework-independent file format for the semantic structure of AI workflows.**

`.aiflow` is not a diagram file. It is the semantic representation of an AI workflow
that *can* be rendered as a diagram.

## The problem

AI workflows are fragmented across code, Markdown, Mermaid, JSON/YAML, framework
graphs, and diagram tools. None of them connects:

```
components → relationships → data flow → behavior → source code → AI context
```

Diagrams drift from code. Framework graphs do not survive a framework change. Neither
is answerable by a model.

## The model

```
AIFLOW = Nodes + Semantic Edges + Registries + Metadata + Provenance

              AI Project
                  ↓
          AIFLOW Semantic Graph
                  ↓
   ┌──────────────┬──────────────┬──────────────┐
   │ Visualization│ AI Reasoning │ Documentation│
   └──────────────┴──────────────┴──────────────┘
```

**Nodes** — `agent` `llm` `tool` `prompt` `retriever` `vector_store` `input` `output` `condition`

**Edges** — `calls` `uses` `retrieves` `passes` `produces` `routes_to`

Edge type carries meaning. `calls` transfers control; `uses` declares a dependency;
`passes` moves data between named ports. A renderer can draw all three the same way —
a model reasoning about failure paths cannot.

## What is in this repo

| Path | |
|---|---|
| [`spec/SPEC.md`](spec/SPEC.md) | The v1 specification |
| [`spec/aiflow-v1.schema.json`](spec/aiflow-v1.schema.json) | JSON Schema (draft 2020-12) — structural validation |
| [`spec/edge-compatibility.json`](spec/edge-compatibility.json) | Normative edge/node type matrix — machine-readable |
| [`examples/rag-support-agent.aiflow`](examples/rag-support-agent.aiflow) | Reference document exercising every node and edge type |
| [`examples/sample-project/`](examples/sample-project) | A small Python AI app, used as the analyzer's fixture |
| [`aiflow/`](aiflow) | Python SDK and CLI — model, validator, graph queries, diff, renderer, analyzer |
| [`tests/`](tests) | Conformance suites |

## Install

```bash
pip install -e .
```

## Use it

```bash
aiflow generate ./my-ai-project -o project.aiflow
aiflow validate examples/rag-support-agent.aiflow --strict
aiflow inspect examples/rag-support-agent.aiflow --paths --unhandled
aiflow render examples/rag-support-agent.aiflow --open
aiflow init -o project.aiflow
aiflow diff old.aiflow new.aiflow
```

## Generate

```bash
aiflow generate ./my-ai-project
```

Walks the project's syntax tree and extracts agents, LLM calls, prompts, tools,
retrievers, vector stores, and the data flow between them — with a source reference
and a confidence on every claim.

```
$ aiflow generate examples/sample-project
wrote project.aiflow
  analyzed 8 file(s) -> 11 nodes, 10 edges
  agent=2, input=1, llm=2, output=1, prompt=2, retriever=1, tool=1, vector_store=1
  note branching is not extracted by static analysis; add condition nodes by hand
```

**What it will not do.** It reports what the syntax tree shows and nothing else. A
class becomes an `agent` because it contains an LLM invocation, never because it is
called `SupervisorAgent`. Two components are linked by `passes` only when one call
provably consumes a value another produced — not because they sit in the same
function. And it emits no `ai_context` at all: intent, summaries, and failure modes
are the semantic analyzer's job, and asserting them here would put guesses behind a
label that means *parsed*. Where it cannot see something — runtime-assembled graphs,
branching — it says so rather than inventing it.

## Render

```bash
aiflow render project.aiflow -o workflow.html
```

One self-contained HTML file — no CDN, no build step, no server. It opens from a
`file://` path or a CI artifact. The page gives you a pan/zoom graph laid out by
edge semantics, a searchable component explorer, per-node detail with **clickable
source permalinks pinned to the commit**, filter chips per edge type, and path
highlighting from input to output. Nodes carrying an unhandled failure or an
AI-inferred claim are flagged in the graph itself.

Layout is computed at render time, never stored in the document — the spec treats
layout as presentation and the semantic model as authoritative.

`aiflow inspect` answers behavioral questions directly against the graph:

```
$ aiflow inspect examples/rag-support-agent.aiflow --paths

Paths to output
  in_user_query(input) --passes--> agent_supervisor(agent) --calls--> llm_router(llm)
    --passes--> cond_route(condition) --routes_to--> retr_kb(retriever)
    --passes--> agent_answerer(agent) --calls--> llm_answer(llm) --produces--> out_response(output)
  ... 2 more, one per branch
```

```
$ aiflow inspect examples/rag-support-agent.aiflow --unhandled

Unhandled failure modes
  agent_supervisor: Router LLM times out; no fallback route is configured.
  retr_kb: Vector store unreachable; the call raises and the graph aborts without a degraded path.
```

| Flag | Question it answers |
|---|---|
| `--paths` | Show all paths to the final response |
| `--rag` | Where is RAG used? |
| `--tools` | Which agents use external tools? |
| `--unhandled` | Find missing error handling |
| `--inferred` | Which claims were guessed rather than parsed? |
| `--node ID` | What is this component, and what connects to it? |

## As a library

```python
from aiflow import Document, Graph, diff

doc = Document.load("project.aiflow")
g = Graph(doc)

for path in g.paths_to_outputs():
    print(path.render(doc))

# what stops working if the vector store fails
print([n.id for n in g.impact_of("vs_kb")])

# claims a model inferred rather than parsed, not yet human-reviewed
print(g.low_trust())
```

## Tests

```bash
python3 tests/run_all.py
```

687 assertions across four suites. The matrix suite sweeps all 486
`edge_type × source_type × target_type` combinations against the compatibility
matrix in both directions, and runs 25 targeted mutations each expected to raise a
specific diagnostic. The SDK suite covers lossless round-tripping, graph queries,
diff semantics, and CLI exit codes. The render suite covers layering (including
cyclic workflows), determinism, and that the page stays self-contained and escapes
untrusted document content. The analyzer suite pins each detection rule, the
cross-file links, and the boundary above — including a test asserting that nothing
generated ever carries invented `ai_context`.

## Two things that make this different

**Provenance is structural, not advisory.** Anything marked `ai_inference` is
*required* by the schema to carry a confidence score. A model cannot silently assert
workflow structure as fact, and `aiflow inspect --inferred` lists every claim that was
guessed rather than parsed and has not yet been human-reviewed.

**Occurrences are separate from definitions.** Nodes are positions in the graph;
prompts, models, tools, and data sources are declared once in registries and
referenced. Retargeting an index or editing a prompt is a single-site change, and
`aiflow diff` reports it once instead of once per call site — there is a test that
pins exactly this.

## Validation is two-tier

JSON Schema cannot dereference an id to its node type, so:

- **L1 structural** — shape, enums, required fields, conditional requirements → JSON Schema
- **L2 semantic** — referential integrity, edge compatibility, port bindings, reachability, provenance discipline → validator

A document is conformant only when it passes both. Diagnostic codes are listed in
[the spec](spec/SPEC.md#diagnostic-codes).

## Roadmap

| Phase | | Status |
|---|---|---|
| 0 | Research & competitive analysis | — |
| 1 | AIFLOW specification | ✅ |
| 2 | JSON Schema | ✅ |
| 3 | Python SDK | ✅ |
| 4 | Interactive renderer | ✅ |
| 5 | Python code analyzer | ✅ |
| 6 | AI semantic analyzer | next |
| 7 | Framework adapters — LangGraph → LangChain → OpenAI Agents SDK → CrewAI → LlamaIndex | |
| 8 | AI-powered workflow exploration | |
| 9 | Git / developer integration | |
| 10 | Ecosystem & open specification | |

### CLI

| Command | Status |
|---|---|
| `aiflow init` | ✅ |
| `aiflow validate` | ✅ |
| `aiflow inspect` | ✅ |
| `aiflow diff` | ✅ |
| `aiflow render` | ✅ |
| `aiflow generate` | ✅ — plain Python; framework adapters are phase 7 |

## v1 success criteria

Point AIFLOW at an AI project and get:

1. A structured `.aiflow` file
2. A useful interactive workflow diagram
3. Code-to-workflow traceability
4. Validation of the workflow

## License

Apache License 2.0 — see [LICENSE](LICENSE).
