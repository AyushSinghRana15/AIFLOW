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

__all__ = ["Change", "DiffResult", "diff"]

_KINDS = ("nodes", "edges") + REGISTRY_KEYS


@dataclass(frozen=True)
class Change:
    kind: str          # nodes | edges | prompts | models | tools | data_sources
    op: str            # added | removed | modified
    id: str
    field: str | None = None
    before: object = None
    after: object = None

    def __str__(self) -> str:
        head = f"{self.op:8} {self.kind[:-1]:11} {self.id}"
        if self.op != "modified":
            return head
        return f"{head}\n           {self.field}: {self.before!r} -> {self.after!r}"


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
