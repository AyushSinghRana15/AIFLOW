---
name: Specification change
about: Propose a change to the .aiflow format
labels: spec
---

**What the format cannot currently express**

Describe the workflow or claim that has nowhere to go today.

**Proposed change**

Schema shape, and an example document fragment.

**Compatibility**

- [ ] Additive — no conformant `1.x` document becomes invalid (minor version bump)
- [ ] Breaking — would invalidate existing documents (requires v2 and discussion)

**Which layer would emit this?**

Static analysis, a framework adapter, runtime tracing, model inference, or a human?
This determines what provenance it would carry, and whether it can be trusted as fact.

**Is it semantic or presentational?**

The specification's boundary is that the semantic model is authoritative and layout is
presentation. Anything about appearance belongs in `metadata.layout` or `extensions`.
