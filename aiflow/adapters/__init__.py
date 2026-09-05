"""Framework adapters.

Register an adapter here and the analyzer will use it whenever its framework is
detected. See `docs/ADAPTERS.md` for how to write one.
"""
from __future__ import annotations

from pathlib import Path

from ..analyze.python import FileReport
from .base import Adapter, AdapterResult, PROVENANCE_METHOD
from .crewai import CrewAIAdapter
from .langchain import LangChainAdapter
from .langgraph import LangGraphAdapter
from .llamaindex import LlamaIndexAdapter
from .openai_agents import OpenAIAgentsAdapter

__all__ = ["Adapter", "AdapterResult", "PROVENANCE_METHOD",
           "ADAPTERS", "detect", "run",
           "LangGraphAdapter", "LangChainAdapter", "OpenAIAgentsAdapter",
           "CrewAIAdapter", "LlamaIndexAdapter"]

ADAPTERS: list[Adapter] = [
    LangGraphAdapter(),
    OpenAIAgentsAdapter(),
    CrewAIAdapter(),
    LlamaIndexAdapter(),
    LangChainAdapter(),
]


def detect(reports: list[FileReport]) -> list[Adapter]:
    """Adapters whose framework appears in the analyzed project."""
    return [a for a in ADAPTERS if a.detects(reports)]


def run(root: str | Path, reports: list[FileReport]) -> list[AdapterResult]:
    """Run every applicable adapter."""
    return [a.analyze(Path(root), reports) for a in detect(reports)]
