# Writing a framework adapter

The generic Python analyzer sees what any Python file shows: calls, classes,
constants. It cannot see **topology**, because topology in an AI framework is
expressed through that framework's own vocabulary. `add_conditional_edges` means
"branch here" only if you already know LangGraph.

An adapter is the component that knows one framework's vocabulary.

## What an adapter is for

| Layer | Sees | Provenance it claims |
|---|---|---|
| Generic analyzer | LLM calls, prompts, tools, stores, classes, data flow | `static_analysis` |
| **Adapter** | Graph structure, branches, entry and terminal points | `framework_adapter` |
| Semantic analyzer | Intent, failure modes, invariants | `ai_inference` |

The provenance distinction is not cosmetic. A `routes_to` edge parsed from a real
`add_conditional_edges` call is a firmer claim about branching than generic analysis
could ever make, and a reader must be able to tell which produced it.

**Adapters are the only component allowed to emit `condition` nodes and `routes_to`
edges.** The generic analyzer refuses to guess at branching; an adapter does not have
to guess, because the framework states it.

## The contract

```python
class MyAdapter:
    name = "aiflow-adapter-myframework"   # recorded in project.frameworks[].adapter
    framework = "myframework"             # the key the generic analyzer reports

    def detects(self, reports: list[FileReport]) -> bool:
        return any(self.framework in r.frameworks for r in reports)

    def analyze(self, root: Path, reports: list[FileReport]) -> AdapterResult:
        ...
```

Register it in [`aiflow/adapters/__init__.py`](../aiflow/adapters/__init__.py):

```python
ADAPTERS: list[Adapter] = [
    LangGraphAdapter(),
    MyAdapter(),
]
```

For `detects` to work, the framework's import root must map to a framework key in
[`aiflow/analyze/signatures.py`](../aiflow/analyze/signatures.py):

```python
FRAMEWORKS = {..., "myframework": "myframework"}
```

## What `analyze` returns

An `AdapterResult` carrying `Finding` objects. The assembler understands five kinds:

| Kind | `data` | Becomes |
|---|---|---|
| `fw_node` | `{builder, label, target}` | A graph step. Typed `retriever` if its handler only retrieves, else `agent`. |
| `fw_edge` | `{builder, source, target}` | A `passes` edge between two steps |
| `fw_branch` | `{builder, source, router, mapping, partial}` | A `condition` node plus one `routes_to` edge per mapping entry |
| `fw_entry` | `{builder, label}` | An `input` node and a `passes` edge into the step |
| `fw_terminal` | `{builder, label}` | An `output` node and a `produces` edge from the step |

`target` is the handler's function name. The assembler uses it to attach whatever the
generic pass found inside that function — the LLM call it makes, the prompt it
references, the store it queries — to the step that owns it.

## The rule that matters

**Report what you cannot see.** Frameworks are frequently wired from variables:

```python
builder.add_conditional_edges("classify", router, ROUTES)   # ROUTES is not a literal
```

An AST walk cannot resolve `ROUTES`. The adapter must:

1. **not** invent the routes,
2. mark the branch `partial: True`,
3. lower its confidence,
4. append a note naming the file and line.

A graph that is quietly missing three branches is worse than one that says three
branches are missing. The LangGraph adapter has tests for each of these; see
`suite_honesty` in [`tests/test_adapters.py`](../tests/test_adapters.py).

## Testing

Add a fixture project under `examples/` written the way a real user would write it,
not the way that is easiest to parse. Then assert:

- each construct is recognised in isolation,
- non-literal forms produce a note and no invented structure,
- the assembled document **validates with no warnings**,
- adapter-derived elements claim `framework_adapter` while generic ones still claim
  `static_analysis`,
- a project *without* the framework is byte-identical with and without the adapter
  registered (`suite_isolation`).

That last one is the regression that matters: an adapter must never change a project
it does not apply to.

## Status

| Framework | Adapter | Reads | Reports as unreadable |
|---|---|---|---|
| LangGraph | [`langgraph.py`](../aiflow/adapters/langgraph.py) | `add_node`, `add_edge`, `add_conditional_edges`, `START`/`END` | non-literal names, endpoints, route maps |
| LangChain | [`langchain.py`](../aiflow/adapters/langchain.py) | LCEL pipes, `RunnableBranch` | chains with no declared ordering, `RunnableLambda` bodies |
| OpenAI Agents SDK | [`openai_agents.py`](../aiflow/adapters/openai_agents.py) | `Agent(tools=, handoffs=)`, `Runner.run` | handoff lists built at runtime |
| CrewAI | [`crewai.py`](../aiflow/adapters/crewai.py) | `Task(agent=, context=)`, `Crew(process=)` | `Process.hierarchical`, non-literal task lists |
| LlamaIndex | [`llamaindex.py`](../aiflow/adapters/llamaindex.py) | `QueryPipeline.add_modules`, `add_link` | non-literal module maps, legacy query-engine chaining |

The fourth column is not an afterthought. It is the part of an adapter that keeps the
format trustworthy.

Contributions welcome — see [CONTRIBUTING.md](../CONTRIBUTING.md).
