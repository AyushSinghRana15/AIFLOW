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
| [`tools/validate.py`](tools/validate.py) | Two-level validator |
| [`tests/test_matrix.py`](tests/test_matrix.py) | Conformance suite |

## Try it

```bash
pip install jsonschema
python3 tools/validate.py examples/rag-support-agent.aiflow
```

```bash
python3 tests/test_matrix.py
```

The suite runs 542 assertions: the reference document validates clean, an exhaustive
sweep of all 486 `edge_type × source_type × target_type` combinations agrees with the
compatibility matrix in both directions, and 25 targeted mutations each raise their
expected diagnostic.

## Two things that make this different

**Provenance is structural, not advisory.** Anything marked `ai_inference` is
*required* by the schema to carry a confidence score. A model cannot silently assert
workflow structure as fact, and a reviewer can always separate what was parsed from
what was guessed.

**Occurrences are separate from definitions.** Nodes are positions in the graph;
prompts, models, tools, and data sources are declared once in registries and
referenced. Retargeting an index or editing a prompt is a single-site change, and a
diff reports it once instead of once per call site.

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
| 3 | Python SDK | next |
| 4 | Interactive renderer | |
| 5 | Python code analyzer | |
| 6 | AI semantic analyzer | |
| 7 | Framework adapters — LangGraph → LangChain → OpenAI Agents SDK → CrewAI → LlamaIndex | |
| 8 | AI-powered workflow exploration | |
| 9 | Git / developer integration | |
| 10 | Ecosystem & open specification | |

### Planned CLI

```
aiflow init
aiflow generate .
aiflow validate project.aiflow
aiflow render project.aiflow
aiflow inspect project.aiflow
aiflow diff old.aiflow new.aiflow
```

`aiflow validate` is implemented today as [`tools/validate.py`](tools/validate.py).

## v1 success criteria

Point AIFLOW at an AI project and get:

1. A structured `.aiflow` file
2. A useful interactive workflow diagram
3. Code-to-workflow traceability
4. Validation of the workflow

## License

Apache License 2.0 — see [LICENSE](LICENSE).
