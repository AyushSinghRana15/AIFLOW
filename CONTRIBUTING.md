# Contributing to AIFLOW

Thanks for looking. This project has one unusual rule, and it is the reason most of
the design decisions look the way they do:

> **Never assert more than you can support.** Every claim in a `.aiflow` document
> records how it was learned. A contribution that makes the tool guess while labelling
> the guess as fact will be sent back, however useful the guess is.

## Getting set up

```bash
git clone https://github.com/AyushSinghRana15/AIFLOW.git
cd AIFLOW
pip install -e .
python tests/run_all.py
```

No test needs a network connection or an API key. The semantic analyzer's suite runs
entirely against a fake client — a suite that spends a rate-limited quota is one people
stop running.

## The layout

| Path | |
|---|---|
| `spec/` | The normative specification, schema, and compatibility matrix |
| `aiflow/analyze/` | Generic Python analysis — claims `static_analysis` |
| `aiflow/adapters/` | Framework-specific topology — claims `framework_adapter` |
| `aiflow/semantic/` | Model inference — claims `ai_inference` |
| `aiflow/` | Model, validator, graph queries, diff, renderers, CLI |
| `docs/` | Adapter guide, conformance, generated diagrams |

The three analysis layers are separated by *what they are allowed to claim*, not by
convenience. Keep changes on the right side of that line.

## Common contributions

### Supporting a new provider or library

Usually a table edit in [`aiflow/analyze/signatures.py`](aiflow/analyze/signatures.py).
Add the call signature with a confidence that reflects how unambiguous it is: an exact
method chain scores high, a bare verb like `search` scores low or requires a known
receiver. Add a case to `suite_detection` in `tests/test_analyze.py`, including a
negative one — something that looks similar and must *not* match.

### Writing a framework adapter

See [`docs/ADAPTERS.md`](docs/ADAPTERS.md). Adapters are the only component permitted
to emit `condition` nodes and `routes_to` edges, because they are the only one that
does not have to guess about branching.

The bar: a fixture project written the way a real user would write it, and a test
asserting the assembled document validates with **zero warnings**. Plus the isolation
test — a project not using your framework must be byte-identical with your adapter
registered or not.

### Changing the specification

Additive changes bump the minor version and update, together:

1. `spec/aiflow-v1.schema.json`
2. `spec/SPEC.md`, including the version table
3. `aiflow/model.py`, keeping the round trip lossless
4. `docs/CONFORMANCE.md` if reader or writer obligations change

Anything that would invalidate a conformant `1.x` document requires v2 and a
discussion first — open an issue rather than a pull request.

If you add an edge type, extend `examples/rag-support-agent.aiflow` too. The
compatibility matrix sweep reads the matrix as ground truth, so the reference example
is the only thing anchoring the matrix to reality.

## Testing

```bash
python tests/run_all.py         # everything
python tests/test_adapters.py   # one suite
python docs/build.py            # regenerate README diagrams
```

Two habits this codebase relies on:

**Verify, do not assume.** Several bugs here were caught only by rendering the page in
a browser, calling the real API, or copying a fixture to a different directory. If a
change affects output someone will look at, look at it.

**Write the negative control.** A test that cannot fail is not a test. When adding a
linter or a matrix check, break the thing on purpose and confirm the test catches it.

## Pull requests

- One concern per pull request.
- Say what you verified and how, especially if it was manual.
- If you found a limitation you did not fix, write it down — in a docstring, in the
  README, or in the code. An acknowledged gap is worth more than a silent one.
- CI runs on Python 3.10–3.13, builds the wheel, installs it outside the checkout, and
  checks the README diagrams and Mermaid blocks. All of it must pass.

## Reporting a security issue

Do not open a public issue for a vulnerability. Email the address on the
[maintainer's profile](https://github.com/AyushSinghRana15) instead.

Note that `.aiflow` documents are untrusted input: they may come from someone else's
repository. The renderer escapes document content and refuses non-`http(s)` link
targets. Anything that lets a document execute code or reach the network when rendered
is a security bug.
