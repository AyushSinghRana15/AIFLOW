"""AIFLOW -- a framework-independent semantic representation of AI workflows.

`.aiflow` is not a diagram file. It is the semantic representation of an AI
workflow that can be rendered as a diagram.

    from aiflow import Document, Graph

    doc = Document.load("project.aiflow")
    g = Graph(doc)
    for path in g.paths_to_outputs():
        print(path.render(doc))
"""
from .diff import Change, DiffResult, diff
from .graph import Finding, Graph, Path
from .model import (
    AIContext, DataSource, Document, Edge, FailureMode, Framework, ModelDef,
    Node, Port, Project, Prompt, Provenance, SourceRef, ToolDef,
)
from .spec import SPEC_VERSION, edge_matrix, edge_rules, edge_types, node_types, schema
from .validate import Report, validate, validate_file

__version__ = "0.1.0"

__all__ = [
    "Document", "Node", "Edge", "Port", "Prompt", "ModelDef", "ToolDef", "DataSource",
    "Project", "Framework", "Provenance", "SourceRef", "AIContext", "FailureMode",
    "Graph", "Path", "Finding",
    "diff", "Change", "DiffResult",
    "validate", "validate_file", "Report",
    "schema", "edge_matrix", "edge_rules", "edge_types", "node_types", "SPEC_VERSION",
    "__version__",
]
