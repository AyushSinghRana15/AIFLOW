# Conformance

This document defines what it means to implement AIFLOW. It is written for people
building a reader, writer, or adapter in another language — not only for users of the
Python reference implementation.

The normative artifacts are:

| Artifact | Role |
|---|---|
| [`spec/SPEC.md`](../spec/SPEC.md) | The specification |
| [`spec/aiflow-v1.schema.json`](../spec/aiflow-v1.schema.json) | Structural validation (JSON Schema draft 2020-12) |
| [`spec/edge-compatibility.json`](../spec/edge-compatibility.json) | The edge-type / node-type matrix |
| [`examples/rag-support-agent.aiflow`](../examples/rag-support-agent.aiflow) | The reference document |

An implementation MUST load `edge-compatibility.json` rather than hardcoding the
matrix. It is machine-readable precisely so that adding an edge type does not require
every implementation to be edited by hand.

## The two levels

JSON Schema cannot dereference an id to its node type, so validation is necessarily
split. A document is **conformant** only when it passes both.

### L1 — structural

Validate against `aiflow-v1.schema.json`. Nothing further is required; any draft
2020-12 validator will do. Report failures as `AF100`.

### L2 — semantic

These rules cannot be expressed in JSON Schema. An implementation claiming L2 support
MUST implement all of them, with these codes:

| Code | Rule |
|---|---|
| `AF201` `AF202` `AF203` | Node, edge, and registry ids are unique within their collection |
| `AF210` `AF211` | Every edge endpoint names a declared node |
| `AF220` `AF221` | Every `ref` resolves in the registry its node type selects, and node types that take no `ref` do not carry one |
| `AF230` | Edge endpoints satisfy the compatibility matrix |
| `AF240` `AF241` | Named ports exist on the referenced node |
| `AF250` `AF251` | A condition has at least two branches (warning) and at most one default (error) |
| `AF260` | `depends_on` resolves |
| `AF270` `AF271` | Prompt `source_node` and failure-mode `handler_node` resolve |
| `AF280`–`AF283` | Reachability from inputs to outputs |
| `AF290` `AF291` | Inferred claims carry adequate confidence and evidence (warnings) |

Severities are normative: an implementation MUST NOT downgrade an error to a warning.
It MAY add codes outside the `AF` prefix.

## Reader obligations

A conformant reader:

1. **MUST** accept any `1.x` document, including minor versions newer than it knows.
2. **MUST** ignore unrecognised keys inside `extensions` rather than failing.
3. **MUST NOT** treat `metadata.stats` as authoritative — it is a cache; the arrays
   are the truth.
4. **MAY** discard `metadata.layout` entirely. Layout is presentation; the semantic
   model is authoritative. A reader that renders is free to compute its own.
5. **MUST** preserve `provenance` when transforming a document. Dropping it converts
   an inference into an apparent fact, which is the one failure mode the format exists
   to prevent.

## Writer obligations

A conformant writer:

1. **MUST** emit `provenance.confidence` whenever `method` is `ai_inference`. This is
   enforced structurally by the schema, but it is stated here because it is the point.
2. **MUST** use the narrowest true `method`. Do not label an inference
   `static_analysis` because it is more convincing.
3. **SHOULD** attach `source` references wherever a claim came from code, and
   `provenance.evidence` wherever a claim is supported by one.
4. **MUST** declare `1.1` when using `ai_context.provenance`.
5. **SHOULD** report what it could not extract rather than emitting a graph that reads
   as complete. A workflow silently missing three branches is worse than one that says
   three branches are missing.

## Round-tripping

Decoding and re-encoding a conformant document MUST be lossless. Specifically, a field
that is **absent** must stay absent, and a field that is **present but empty** must
stay empty. Collapsing the two is the usual cause of a diff tool reporting phantom
changes.

The reference implementation asserts this against the reference document; a port
should do the same.

## Provenance methods

| Method | Means | Typical emitter |
|---|---|---|
| `static_analysis` | Derived by parsing code | Language analyzer |
| `framework_adapter` | Reported by a framework-specific extractor | Adapter |
| `runtime_trace` | Observed during execution | Instrumentation |
| `ai_inference` | Inferred by a model | Semantic analyzer |
| `manual` | Authored by a human | A person |

`condition` nodes and `routes_to` edges SHOULD only be emitted with
`framework_adapter`, `runtime_trace`, or `manual` provenance. Generic static analysis
cannot see branching, and a `static_analysis` branch claim is almost always a guess
wearing the wrong label.

## Test vectors

The reference document exercises all nine node types and all six edge types. An
implementation should be able to:

- validate it with zero errors and zero warnings,
- round-trip it byte-identically,
- report exactly three paths from input to output,
- report exactly two unhandled failure modes.

The generated fixtures under [`examples/`](../examples) are secondary vectors covering
the analyzer and the LangGraph adapter.

## Registering an implementation

Open a pull request adding it to the table below.

| Implementation | Language | Levels | Status |
|---|---|---|---|
| [`aiflow-format`](https://pypi.org/project/aiflow-format/) | Python | L1 + L2 | Reference |
