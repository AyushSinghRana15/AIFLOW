"""Framework adapter protocol.

The generic Python analyzer sees what any Python file shows: calls, classes,
constants. It cannot see *topology*, because topology in an AI framework is
expressed through that framework's own API — `add_conditional_edges` means
"branch here" only if you know LangGraph.

An adapter is the component that knows one framework's vocabulary. It runs
after the generic pass and contributes what only it can: graph structure,
branch conditions, entry and terminal points.

Adapters emit `framework_adapter` provenance rather than `static_analysis`.
The distinction is not cosmetic: a `routes_to` edge asserted by an adapter that
parsed a real `add_conditional_edges` call is a stronger claim than anything
generic analysis could make about branching, and a reader should be able to
tell which produced it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..analyze.python import Finding, FileReport

__all__ = ["Adapter", "AdapterResult", "PROVENANCE_METHOD"]

PROVENANCE_METHOD = "framework_adapter"


class AdapterResult:
    """What an adapter contributes to the document."""

    def __init__(self, name: str, findings: list[Finding] | None = None,
                 version: str | None = None, notes: list[str] | None = None):
        self.name = name
        self.findings = findings or []
        self.version = version
        self.notes = notes or []

    def __repr__(self) -> str:
        return f"AdapterResult({self.name!r}, {len(self.findings)} findings)"


@runtime_checkable
class Adapter(Protocol):
    """A framework-specific extractor.

    Implementations live in `aiflow/adapters/` and are registered in
    `aiflow/adapters/__init__.py`. See `docs/ADAPTERS.md` for a walkthrough.
    """

    name: str
    """Stable identifier, recorded in `project.frameworks[].adapter`."""

    framework: str
    """The framework key the generic analyzer reports on detecting an import."""

    def detects(self, reports: list[FileReport]) -> bool:
        """True when this adapter's framework is present in the project."""
        ...

    def analyze(self, root: Path, reports: list[FileReport]) -> AdapterResult:
        """Extract framework-specific structure."""
        ...
