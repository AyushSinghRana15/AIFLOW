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
| [`aiflow/`](aiflow) | Python SDK and CLI — model, validator, graph queries, diff |
| [`tests/`](tests) | Conformance suites |

## Install

```bash
pip install -e .
```

## Use it

```bash
aiflow validate examples/rag-support-agent.aiflow --strict
aiflow inspect examples/rag-support-agent.aiflow --paths --unhandled
aiflow init -o project.aiflow
aiflow diff old.aiflow new.aiflow
```

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

606 assertions across two suites. The matrix suite sweeps all 486
`edge_type × source_type × target_type` combinations against the compatibility
matrix in both directions, and runs 25 targeted mutations each expected to raise a
specific diagnostic. The SDK suite covers lossless round-tripping, graph queries,
diff semantics, and CLI exit codes.

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
| 4 | Interactive renderer | next |
| 5 | Python code analyzer | |
| 6 | AI semantic analyzer | |
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
| `aiflow render` | phase 4 — exits 2 |
| `aiflow generate` | phases 5–7 — exits 2 |

Unimplemented subcommands exit 2 with an explanation rather than pretending to work.

## v1 success criteria

Point AIFLOW at an AI project and get:

1. A structured `.aiflow` file
2. A useful interactive workflow diagram
3. Code-to-workflow traceability
4. Validation of the workflow

## License

Apache License 2.0 — see [LICENSE](LICENSE).
