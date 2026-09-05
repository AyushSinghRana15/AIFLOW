---
name: Framework adapter request
about: Ask for support for a framework, or propose writing one
labels: adapter
---

**Framework**

Name, version, and a link.

**How it declares its workflow**

A short, real code sample — the way someone would actually write it, not the
simplest possible form.

```python

```

**What it states explicitly**

Which of these does the framework declare in source, as opposed to assembling at
runtime?

- [ ] Steps / nodes
- [ ] Sequential edges
- [ ] Conditional branches, with their conditions
- [ ] Entry and terminal points
- [ ] Tools bound to an agent

Anything assembled at runtime cannot be extracted by static analysis, and an adapter
will report it as missing rather than guess.

**Are you offering to write it?**

`docs/ADAPTERS.md` documents the contract; the LangGraph adapter is a worked example.
