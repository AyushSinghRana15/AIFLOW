"""Semantic diff between two AIFLOW documents.

Comparison is by **id**, not by position, so reordering an array is not a
change. Because reusable components live in registries rather than being
inlined per call site, editing one prompt reports as a single change here
regardless of how many nodes reference it -- that is the practical payoff
of the occurrence/definition split.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Document
from .spec import REGISTRY_KEYS

__all__ = ["Change", "DiffResult", "diff", "normalize", "VOLATILE"]

# Fields that change on every run without the workflow having changed. Comparing
# them would make every drift check fail for reasons nobody cares about.
VOLATILE = {
    "project": ("commit", "repository"),
    "metadata": ("generated_at", "files_analyzed", "skipped"),
}


def _strip_lines(refs) -> None:
    """Drop line numbers from a list of source references.

    Inserting one line at the top of a file shifts every reference below it,
    which would report a dozen changes for an edit that changed nothing about
    the workflow. File and symbol are kept, so a component genuinely moving is
    still a change.

    Guarded on the list type on purpose: `Edge.source` is a node id, not a
    source reference, and iterating it would walk its characters.
    """
    if not isinstance(refs, list):
        return
    for ref in refs:
        if hasattr(ref, "start_line"):
            ref.start_line = None
            ref.end_line = None


def normalize(doc: Document) -> Document:
    """A copy with run-to-run noise removed, for drift comparison."""
    import copy
    clone = Document.from_dict(copy.deepcopy(doc.to_dict()))
    if clone.project:
        for field_name in VOLATILE["project"]:
            setattr(clone.project, field_name, None)
    if clone.metadata:
        clone.metadata = {k: v for k, v in clone.metadata.items()
                          if k not in VOLATILE["metadata"]}
    everything = (list(clone.nodes) + list(clone.edges)
                  + [i for key in REGISTRY_KEYS for i in clone.registry(key)])
    for element in everything:
        prov = getattr(element, "provenance", None)
        if prov is not None:
            prov.generated_at = None
            _strip_lines(prov.evidence)
        _strip_lines(getattr(element, "source", None))
        _strip_lines(getattr(element, "source_ref", None))
    return clone

_KINDS = ("nodes", "edges") + REGISTRY_KEYS
_MAX_VALUE = 90


def _brief(value: object) -> str:
    """A one-line rendering. Full provenance dicts make a diff unreadable."""
    text = repr(value)
    return text if len(text) <= _MAX_VALUE else text[:_MAX_VALUE - 1] + "…"


@dataclass(frozen=True)
class Change:
    kind: str          # nodes | edges | prompts | models | tools | data_sources
    op: str            # added | removed | modified
    id: str
    field: str | None = None
    before: object = None
    after: object = None

    @property
    def noun(self) -> str:
        """Singular form of the kind. 'document' is already singular."""
        return self.kind[:-1] if self.kind.endswith("s") else self.kind

    def __str__(self) -> str:
        head = f"{self.op:8} {self.noun:11} {self.id}"
        if self.op != "modified":
            return head
        return f"{head}\n           {self.field}: {_brief(self.before)} -> {_brief(self.after)}"


@dataclass
class DiffResult:
    changes: list[Change]

    def __bool__(self) -> bool:
        return bool(self.changes)

    def by_op(self, op: str) -> list[Change]:
        return [c for c in self.changes if c.op == op]

    def summary(self) -> dict:
        out: dict[str, dict[str, int]] = {}
        for c in self.changes:
            out.setdefault(c.kind, {"added": 0, "removed": 0, "modified": 0})[c.op] += 1
        return out


def _index(doc: Document, kind: str) -> dict[str, dict]:
    items = doc.nodes if kind == "nodes" else doc.edges if kind == "edges" else doc.registry(kind)
    return {i.id: i.to_dict() for i in items}


def _compare(before: dict, after: dict, kind: str, item_id: str) -> list[Change]:
    changes = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b != a:
            changes.append(Change(kind, "modified", item_id, key, b, a))
    return changes


def diff(old: Document, new: Document) -> DiffResult:
    """Compare two documents element by element."""
    changes: list[Change] = []

    for kind in _KINDS:
        before, after = _index(old, kind), _index(new, kind)
        for item_id in sorted(set(after) - set(before)):
            changes.append(Change(kind, "added", item_id))
        for item_id in sorted(set(before) - set(after)):
            changes.append(Change(kind, "removed", item_id))
        for item_id in sorted(set(before) & set(after)):
            changes.extend(_compare(before[item_id], after[item_id], kind, item_id))

    for key in ("version", "metadata"):
        b, a = getattr(old, key), getattr(new, key)
        if b != a:
            changes.append(Change("document", "modified", key, key, b, a))

    if (old.project.to_dict() if old.project else None) != (new.project.to_dict() if new.project else None):
        ob = old.project.to_dict() if old.project else {}
        nb = new.project.to_dict() if new.project else {}
        for key in sorted(set(ob) | set(nb)):
            if ob.get(key) != nb.get(key):
                changes.append(Change("document", "modified", f"project.{key}",
                                      key, ob.get(key), nb.get(key)))

    return DiffResult(changes)
