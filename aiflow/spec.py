"""Locate and load the normative AIFLOW spec artifacts.

The JSON Schema and the edge-compatibility matrix are the normative
artifacts of this repository; they live in `spec/` at the repo root and are
force-included into the wheel at `aiflow/_spec/` at build time. Resolution
checks the packaged copy first, then the source checkout, so the same code
path works installed and from a clone. There is exactly one copy of each
file in version control.

The packaged directory is `_spec`, not `spec`, deliberately: a data
directory named `spec` alongside this `spec.py` module resolves only by
import precedence (a regular module beats a namespace package), which is
too subtle to rely on.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PKG = Path(__file__).resolve().parent

SCHEMA_FILE = "aiflow-v1.schema.json"
MATRIX_FILE = "edge-compatibility.json"

SPEC_VERSION = "1.0"
SUPPORTED_MAJOR = 1


def _resolve(name: str) -> Path:
    for candidate in (_PKG / "_spec" / name, _PKG.parent / "spec" / name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Could not locate spec artifact {name!r}. Looked in "
        f"{_PKG / '_spec'} and {_PKG.parent / 'spec'}."
    )


@lru_cache(maxsize=None)
def schema() -> dict:
    """The AIFLOW v1 JSON Schema."""
    return json.loads(_resolve(SCHEMA_FILE).read_text())


@lru_cache(maxsize=None)
def edge_matrix() -> dict:
    """The normative edge-type / node-type compatibility matrix."""
    return json.loads(_resolve(MATRIX_FILE).read_text())


def edge_rules() -> dict:
    return edge_matrix()["edges"]


def node_types() -> list[str]:
    return list(edge_matrix()["node_types"])


def edge_types() -> list[str]:
    return list(edge_matrix()["edges"])


# Which registry a node's `ref` resolves against, keyed by node type.
REF_REGISTRY = {
    "llm": "models",
    "tool": "tools",
    "prompt": "prompts",
    "vector_store": "data_sources",
    "retriever": "data_sources",
}

REGISTRY_KEYS = ("prompts", "models", "tools", "data_sources")
