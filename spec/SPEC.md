# AIFLOW v1 Specification

**Status:** Draft · **Version:** 1.0 · **File extension:** `.aiflow` · **Media type:** `application/vnd.aiflow+json`

> `.aiflow` is **not** a diagram file. It is the semantic representation of an AI
> workflow that *can* be rendered as a diagram.

## 1. Core model

```
AIFLOW = Nodes + Semantic Edges + Registries + Metadata + Provenance
```

The central design decision of v1 is the split between **occurrences** and **definitions**:

| Concept | Lives in | Meaning |
|---|---|---|
| Occurrence | `nodes[]` | A position in the workflow graph |
| Definition | `prompts[]`, `models[]`, `tools[]`, `data_sources[]` | A reusable component, declared once |

A node points at its definition with `ref`. One prompt used in three places is
**one** registry entry and **three** nodes — so renaming a model, retargeting an
index, or diffing a prompt is a single-site edit, and `aiflow diff` reports it once
rather than N times.

Which registry a `ref` resolves against is determined by the node's `type`:

| Node type | `ref` resolves against | `ref` required |
|---|---|---|
| `llm` | `models` | yes |
| `tool` | `tools` | yes |
| `prompt` | `prompts` | yes |
| `vector_store` | `data_sources` | yes |
| `retriever` | `data_sources` | optional |
| `agent`, `input`, `output`, `condition` | — | forbidden |

## 2. Node types

`agent` · `llm` · `tool` · `prompt` · `retriever` · `vector_store` · `input` · `output` · `condition`

## 3. Edge types

Edge type carries meaning; it is not decoration.

| Type | Semantics |
|---|---|
| `calls` | Invokes the target, transferring control. The source waits. |
| `uses` | Static dependency, no control transfer. |
| `retrieves` | Retrieval operation against a store. |
| `passes` | Data flow from an output port to an input port. |
| `produces` | Yields a terminal result. |
| `routes_to` | Conditional branch. |

The normative **edge compatibility matrix** — which node types may sit at each end of
each edge type — is machine-readable in [`edge-compatibility.json`](edge-compatibility.json).
SDKs and adapters MUST load that file rather than hardcoding the matrix.

Every `routes_to` edge MUST carry either a `when` predicate or `"default": true`.
A `condition` node MUST have at most one default branch.

## 4. Ports

Nodes declare named `inputs` and `outputs`. A `passes` edge binds `source_port` to
`target_port`, so data flow is precise rather than node-to-node hand-waving. Ports
are what make "show all paths to the final response" answerable.

## 5. Provenance and confidence

Every node, edge, and registry entry MAY carry `provenance`. The document carries one too.

```json
{ "method": "ai_inference", "confidence": 0.71,
  "generator": { "name": "aiflow-semantic-analyzer", "model": "claude-sonnet-4-5" },
  "evidence": [{ "file": "agents/answerer.py", "start_line": 41 }] }
```

`method` is one of `static_analysis`, `framework_adapter`, `runtime_trace`,
`ai_inference`, `manual`.

**The load-bearing rule:** when `method` is `ai_inference`, `confidence` is
**required** — enforced structurally, not by convention. A model cannot silently
assert workflow structure as fact. Inferences below 0.6, or lacking `evidence`
and a `reviewed_by` sign-off, are flagged for review. This is how the format keeps
hallucinated structure distinguishable from parsed structure.

`reviewed_by` promotes a human-confirmed inference without rewriting its `method`,
so the audit trail survives review.

**`ai_context` carries its own provenance** (added in 1.1). Semantics are usually
inferred while the element carrying them was parsed from code, so the two are
recorded separately. Enriching a statically-analysed node therefore never has to
downgrade the node's own claim, and a reader can always tell which half of an
element is fact and which half is inference.

## 6. Source mapping

`source_ref` maps any element back to code: `file`, `start_line`, `end_line`,
`symbol`, `commit`, `url`. Paths are relative to `project.root`. Pinning `commit`
per reference is what makes `aiflow diff` git-aware rather than positional.

## 7. Extensibility

Every object is `additionalProperties: false`, so a misspelled key fails validation
instead of being silently dropped. The single sanctioned escape hatch is the
`extensions` object, present on nodes, edges, and every registry entry. Readers MUST
ignore keys they do not understand.

`metadata.layout` holds optional visual hints. **The semantic model is authoritative;
layout is presentation and MAY be discarded** — this is the boundary that keeps
AIFLOW from degenerating into a diagram format. The reference renderer honours this:
it computes layout from the graph at render time and never reads or writes
coordinates in the document.

## 8. Validation levels

JSON Schema cannot dereference an id to its node type, so validation is two-tier:

| Level | Scope | Implemented by |
|---|---|---|
| **L1 structural** | Shape, types, enums, required fields, conditional requirements | `aiflow-v1.schema.json` |
| **L2 semantic** | Referential integrity, edge compatibility, port bindings, reachability, provenance discipline | `aiflow/validate.py` |

A document is **conformant** only when it passes both.

### Diagnostic codes

| Code | Severity | Meaning |
|---|---|---|
| `AF100` | error | L1 structural violation |
| `AF201`/`AF202`/`AF203` | error | Duplicate node / edge / registry id |
| `AF210`/`AF211` | error | Edge source / target is not a declared node |
| `AF220`/`AF221` | error | `ref` unresolved / not permitted for this node type |
| `AF230` | error | Edge type incompatible with endpoint node type |
| `AF240`/`AF241` | error | `source_port` / `target_port` does not exist |
| `AF250` | warning | Condition node with fewer than two branches |
| `AF251` | error | More than one default branch |
| `AF260` | error | `depends_on` unresolved |
| `AF270`/`AF271` | error | Prompt variable `source_node` / failure-mode `handler_node` unresolved |
| `AF280` | warning | Node unreachable from any `input` |
| `AF281` | error | No `output` reachable from any `input` |
| `AF282`/`AF283` | warning | No `input` / `output` node declared |
| `AF290` | warning | AI inference below the confidence threshold |
| `AF291` | warning | AI inference lacking evidence and human review |

## 9. Versioning

`version` matches `^1\.[0-9]+$`. A v1 reader MUST accept any `1.x` document and MUST
ignore unrecognized keys inside `extensions`. Additive changes bump the minor
version; anything that invalidates a conformant 1.x document requires v2.

| Version | Change |
|---|---|
| **1.1** | Added `ai_context.provenance`. A document using it MUST declare `1.1`. |
| **1.0** | Initial specification. |

## 10. Governance

AIFLOW is an open specification. The normative artifacts are this document,
[`aiflow-v1.schema.json`](aiflow-v1.schema.json), and
[`edge-compatibility.json`](edge-compatibility.json). Implementations MUST load the
matrix rather than hardcoding it.

**How the specification changes.** Additive changes — a new optional property, a new
enum member that no conformant document was forbidden to omit — bump the minor version
and update the schema, this document's version table, the reference implementation's
model, and [`CONFORMANCE.md`](../docs/CONFORMANCE.md) together. Anything that would
invalidate a conformant `1.x` document requires v2, and requires discussion before
implementation.

**What belongs in the specification.** Only the semantic model. Appearance belongs in
`metadata.layout` or `extensions`, and a reader is free to discard both. The line to
hold is that `.aiflow` is not a diagram format; the moment layout becomes normative,
it becomes one.

**What belongs to a layer instead.** The format defines what *can* be said. Which
component is allowed to say it is a separate question, answered by `provenance`:
generic static analysis cannot see branching, so a `condition` node with
`static_analysis` provenance is almost always a guess wearing the wrong label. See
[`CONFORMANCE.md`](../docs/CONFORMANCE.md) for the obligations this places on writers.

**Extending the reference example.** The compatibility matrix's conformance sweep reads
the matrix as ground truth, so it stays green even if the matrix is wrong. The
reference example is the only thing anchoring it to reality. Any change that adds an
edge type MUST extend that example.

## 11. Known limits of v1

- **Static analysis cannot capture intent.** `ai_context` is where intent lives, and
  it is almost always `ai_inference` — carry confidence and evidence accordingly.
- **Dynamic workflows.** Runtime-constructed graphs are representable only as far as
  they are statically visible. `runtime_trace` provenance and `optional` edges are
  the v1 answer; full dynamic capture is deferred.
- **The matrix is normative, not proven.** The conformance sweep verifies that the
  validator agrees with `edge-compatibility.json`; the golden example is what pins
  the matrix to a real workflow.
