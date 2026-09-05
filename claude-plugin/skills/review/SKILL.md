---
name: review
description: Review an AI workflow for missing error handling, unreachable components, single points of failure, and claims that were AI-inferred rather than parsed from code. Use when asked to review, audit, or find risks in an AI/LLM/agent workflow, or "what happens if X fails".
argument-hint: "[project path or .aiflow file]"
arguments: [target]
allowed-tools: Bash(aiflow *), Read, Glob
---

# Review an AI workflow

Target: `$target` (default `.`).

## 1. Check the tool is available

!`command -v aiflow >/dev/null 2>&1 || echo "AIFLOW_NOT_INSTALLED"`

If that printed `AIFLOW_NOT_INSTALLED`, offer `pip install aiflow-format` and stop.

## 2. Get a document to review

If the target is a `.aiflow` file, use it. If it is a directory, extract one first:

```
aiflow generate <path> -o /tmp/aiflow-review.aiflow --force
```

## 3. Gather the evidence

```
aiflow inspect <doc> --unhandled --inferred --tools --rag --paths
```

For a specific component, get its detail and blast radius:

```
aiflow inspect <doc> --node <id>
```

## 4. Review along four axes

**Error handling.** Every entry under "unhandled failure modes" is a declared way
this workflow breaks with nothing catching it. For each, read the source at the
cited location before commenting — the document may be out of date with the code.

**Blast radius.** For each retriever, vector store, and external tool, work out what
stops working when it fails. A store whose failure reaches the output node with no
alternate path is a single point of failure; say so, and point at the branch that is
missing.

**Reachability.** Unreachable nodes are either dead code or a missing edge. Both are
worth reporting, and they are not the same problem.

**Trust.** Anything listed under low-trust claims was inferred by a model, not parsed
from the code. Treat those parts of the graph as unverified: check them against the
source before drawing conclusions from them, and tell the user which conclusions rest
on inferred structure.

## 5. Report

Order findings by what would actually hurt in production, not by how easy they are to
fix. For each: what breaks, under what conditions, where the code is, and what the
fix would be. Where the workflow is fine, say so — do not manufacture findings to
fill a list.

Be explicit about what you could not check. Static analysis does not see
runtime-assembled graphs or branching, so absence of a finding is not evidence of
absence.
