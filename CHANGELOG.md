# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[semantic versioning](https://semver.org/). The **specification** is versioned
separately from the **package** — see [the spec's version table](spec/SPEC.md#9-versioning).

## [Unreleased]

Not yet published to PyPI. Install from source; see
[`docs/RELEASING.md`](docs/RELEASING.md) for what remains.

### Added

- **Specification v1.0** — nodes, semantic edges, registries, provenance, source
  mapping. JSON Schema (draft 2020-12) plus a machine-readable edge-compatibility
  matrix.
- **Specification v1.1** — `ai_context.provenance`, so semantics can record how they
  were learned independently of the element carrying them.
- **Python SDK** — typed model with lossless round-tripping, two-tier validator, graph
  queries, semantic diff.
- **`aiflow` CLI** — `view`, `generate`, `validate`, `inspect`, `ask`, `enrich`,
  `render`, `diff`, `init`.
- **Interactive renderer** — one self-contained HTML file with pan/zoom, component
  explorer, commit-pinned source permalinks, and path highlighting. Plus static SVG
  export in light and dark themes.
- **Python analyzer** — extracts agents, LLM calls, prompts, tools, retrievers, vector
  stores, and data flow, with a source reference and confidence on every claim.
- **Framework adapters** — a registry plus adapters for LangGraph, LangChain (LCEL),
  the OpenAI Agents SDK, CrewAI, and LlamaIndex. Adapters are the only component able
  to extract `condition` nodes and `routes_to` edges.
- **LLM client detection** — `ChatOpenAI(model=…)`, `ChatAnthropic(model=…)` and
  similar constructors, which frameworks use instead of a direct call site.
- **`.env` support** — the key is read from a gitignored `.env` beside the project when
  it is not already in the environment.
- **Semantic analyzer** — model-inferred `ai_context` under a batching, caching, and
  per-day call budget.
- **Question answering** — `aiflow ask`, computing exact answers from the graph where
  one exists and reaching for a model only otherwise.
- **Git integration** — `diff --against` for source drift and `diff --base` for
  comparison against a git ref, plus a GitHub Action and pre-commit hooks.
- **Claude Code plugin** — `map`, `check`, and `review` skills.
- **Documentation** — [conformance](docs/CONFORMANCE.md) for other-language
  implementations, an [adapter guide](docs/ADAPTERS.md), a
  [release runbook](docs/RELEASING.md), and generated diagrams.

[Unreleased]: https://github.com/AyushSinghRana15/AIFLOW/commits/main
