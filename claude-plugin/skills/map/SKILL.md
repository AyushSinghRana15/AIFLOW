---
name: map
description: Extract and explain the AI workflow of a codebase — agents, LLM calls, prompts, tools, retrievers, vector stores, and the data flow between them. Use when asked to map, diagram, visualise, or explain the architecture of an AI, LLM, agent, or RAG project, or when asked "how does this workflow work".
argument-hint: "[path to project, defaults to .]"
arguments: [path]
allowed-tools: Bash(aiflow *), Bash(pip install *), Read, Glob
---

# Map the AI workflow

Target: `$path` (default `.` if empty).

## 1. Check the tool is available

!`command -v aiflow >/dev/null 2>&1 && aiflow --version || echo "AIFLOW_NOT_INSTALLED"`

If that printed `AIFLOW_NOT_INSTALLED`, tell the user the plugin needs the CLI and
offer to run `pip install aiflow-format`. Do not continue until it is installed.

## 2. Extract the workflow

Run the analyzer, writing outside the repo so nothing is added to the user's tree
unless they ask:

```
aiflow generate <path> -o /tmp/aiflow-map.aiflow --force
aiflow inspect /tmp/aiflow-map.aiflow --paths --rag --tools --unhandled --inferred
```

If the project is not Python, say so plainly — the analyzer is Python-only today —
and stop rather than reporting an empty graph as if it were a finding.

## 3. Report what was found

Summarise for the user, in this order:

1. **The shape of the workflow** — entry point, the path(s) to the final response,
   and where it branches.
2. **Components** — agents, the models they call, prompts, tools, retrievers and
   stores. Reference real file paths and line numbers from the document, formatted
   as clickable links, so the user can jump to the code.
3. **Risks** — anything under "unhandled failure modes", plus unreachable nodes.
4. **Confidence** — call out any claim the analyzer flagged as low-confidence.

## 4. Be honest about the limits

The analyzer reads the syntax tree. It cannot see workflows assembled at runtime
from configuration, and it does not extract branching. When the graph looks thinner
than the project really is, say that this is a limit of static analysis rather than
implying the project is simple. Never invent components that were not detected.

## 5. Offer the diagram

Ask whether the user wants an interactive view. If yes:

```
aiflow view <path> --save project.aiflow
```

That opens a pan/zoom graph in the browser with source permalinks. Only write
`project.aiflow` into the repo if the user asked for it.
