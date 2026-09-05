---
name: check
description: Validate .aiflow workflow documents — structure, referential integrity, edge compatibility, reachability, and provenance discipline. Use when asked to check, validate, lint, or verify a .aiflow file, or before committing changes to one.
argument-hint: "[file or glob, defaults to every .aiflow in the repo]"
arguments: [target]
allowed-tools: Bash(aiflow *), Read, Glob, Edit
---

# Validate AIFLOW documents

Target: `$target` (default: every `.aiflow` file in the repository).

## 1. Check the tool is available

!`command -v aiflow >/dev/null 2>&1 || echo "AIFLOW_NOT_INSTALLED"`

If that printed `AIFLOW_NOT_INSTALLED`, offer to run `pip install aiflow-format`
and stop until it succeeds.

## 2. Find and validate

Locate the documents with Glob (`**/*.aiflow`) if no target was given, then:

```
aiflow validate <files> --strict
```

Use `--json` when you need to process the findings rather than show them.

## 3. Explain each finding

Validation is two-tier, and the tier matters when explaining a failure:

- **`AF100`** is structural — the JSON Schema rejected the shape.
- **`AF2xx`** is semantic — the document is well-formed but the graph is wrong.

Common cases:

| Code | Means |
|---|---|
| `AF210` / `AF211` | An edge points at a node that does not exist |
| `AF220` | A node's `ref` does not resolve in its registry |
| `AF230` | That edge type cannot connect those two node types |
| `AF240` / `AF241` | A port named on an edge does not exist on the node |
| `AF251` | A condition has more than one default branch |
| `AF280` / `AF281` | A node is unreachable, or no output is reachable from any input |
| `AF290` / `AF291` | An AI-inferred claim is low-confidence, or cites no evidence |

The full table is in `spec/SPEC.md`. For `AF230`, check
`spec/edge-compatibility.json` for which node types that edge actually permits —
do not guess.

## 4. Fix only what you understand

Offer concrete fixes with Edit. Two rules:

- Never silence a warning by deleting the element that raised it.
- `AF290` and `AF291` are about trust, not syntax. The fix is to add `evidence`, or
  to set `reviewed_by` after a human has actually confirmed the claim — never to
  raise the confidence number to make the warning stop.
