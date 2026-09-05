"""Typed model for AIFLOW v1 documents.

Design constraint: decoding and re-encoding a conformant document is
**lossless**. Absent optional fields decode to `None` and are omitted on
encode; a field present but empty (``[]``) survives as empty. That
distinction is what lets `aiflow diff` compare two documents without
reporting phantom changes introduced by the round trip.

Open-ended objects (`config`, `parameters`, `extensions`, `metadata`,
JSON Schema fragments) are carried through as plain dicts by design --
the format's escape hatches should not be constrained by these classes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

__all__ = [
    "SourceRef", "Provenance", "Port", "FailureMode", "AIContext",
    "Node", "Edge", "Prompt", "ModelDef", "ToolDef", "DataSource",
    "Framework", "Project", "Document",
]


def _pack(**kw: Any) -> dict:
    """Drop absent (None) fields. Empty containers are preserved."""
    return {k: v for k, v in kw.items() if v is not None}


def _many(data: dict, key: str, cls: type) -> list | None:
    raw = data.get(key)
    return None if raw is None else [cls.from_dict(x) for x in raw]


def _dump(items: list | None) -> list | None:
    return None if items is None else [i.to_dict() for i in items]


def _one(data: dict, key: str, cls: type):
    raw = data.get(key)
    return None if raw is None else cls.from_dict(raw)


class _Base:
    """Shared conveniences. Subclasses define from_dict/to_dict explicitly
    so field-to-key mapping stays readable and auditable against the schema."""

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_json(cls, text: str):
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------------------
# leaf types
# ---------------------------------------------------------------------------
@dataclass
class SourceRef(_Base):
    """A mapping back into source code -- the basis of code-to-workflow traceability."""
    file: str
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    language: str | None = None
    commit: str | None = None
    url: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "SourceRef":
        return cls(file=d["file"], start_line=d.get("start_line"), end_line=d.get("end_line"),
                   symbol=d.get("symbol"), language=d.get("language"),
                   commit=d.get("commit"), url=d.get("url"))

    def to_dict(self) -> dict:
        return _pack(file=self.file, start_line=self.start_line, end_line=self.end_line,
                     symbol=self.symbol, language=self.language,
                     commit=self.commit, url=self.url)

    def __str__(self) -> str:
        loc = self.file
        if self.start_line:
            loc += f":{self.start_line}"
            if self.end_line and self.end_line != self.start_line:
                loc += f"-{self.end_line}"
        return loc


@dataclass
class Provenance(_Base):
    """How a claim in the document was obtained."""
    method: str
    confidence: float | None = None
    evidence: list[SourceRef] | None = None
    generator: dict | None = None
    generated_at: str | None = None
    reviewed_by: str | None = None
    notes: str | None = None

    AI_INFERENCE: ClassVar[str] = "ai_inference"

    @property
    def is_inferred(self) -> bool:
        return self.method == self.AI_INFERENCE

    @property
    def is_trusted(self) -> bool:
        """Inferred claims are trusted only once a human has signed off."""
        return not self.is_inferred or self.reviewed_by is not None

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        return cls(method=d["method"], confidence=d.get("confidence"),
                   evidence=_many(d, "evidence", SourceRef), generator=d.get("generator"),
                   generated_at=d.get("generated_at"), reviewed_by=d.get("reviewed_by"),
                   notes=d.get("notes"))

    def to_dict(self) -> dict:
        return _pack(method=self.method, confidence=self.confidence,
                     evidence=_dump(self.evidence), generator=self.generator,
                     generated_at=self.generated_at, reviewed_by=self.reviewed_by,
                     notes=self.notes)


@dataclass
class Port(_Base):
    """A named input or output slot. `passes` edges bind port to port."""
    name: str
    description: str | None = None
    type: str | None = None
    schema: dict | None = None
    required: bool | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Port":
        return cls(name=d["name"], description=d.get("description"), type=d.get("type"),
                   schema=d.get("schema"), required=d.get("required"))

    def to_dict(self) -> dict:
        return _pack(name=self.name, description=self.description, type=self.type,
                     schema=self.schema, required=self.required)


@dataclass
class FailureMode(_Base):
    description: str
    handled: bool | None = None
    handler_node: str | None = None
    source: SourceRef | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "FailureMode":
        return cls(description=d["description"], handled=d.get("handled"),
                   handler_node=d.get("handler_node"), source=_one(d, "source", SourceRef))

    def to_dict(self) -> dict:
        return _pack(description=self.description, handled=self.handled,
                     handler_node=self.handler_node,
                     source=self.source.to_dict() if self.source else None)


@dataclass
class AIContext(_Base):
    """Natural-language semantics -- the layer that answers behavioral questions
    without re-reading the code."""
    summary: str | None = None
    intent: str | None = None
    failure_modes: list[FailureMode] | None = None
    invariants: list[str] | None = None
    side_effects: list[str] | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "AIContext":
        return cls(summary=d.get("summary"), intent=d.get("intent"),
                   failure_modes=_many(d, "failure_modes", FailureMode),
                   invariants=d.get("invariants"), side_effects=d.get("side_effects"),
                   notes=d.get("notes"))

    def to_dict(self) -> dict:
        return _pack(summary=self.summary, intent=self.intent,
                     failure_modes=_dump(self.failure_modes), invariants=self.invariants,
                     side_effects=self.side_effects, notes=self.notes)


# ---------------------------------------------------------------------------
# graph elements
# ---------------------------------------------------------------------------
@dataclass
class Node(_Base):
    """An occurrence of a component in the workflow graph."""
    id: str
    type: str
    name: str | None = None
    description: str | None = None
    ref: str | None = None
    config: dict | None = None
    inputs: list[Port] | None = None
    outputs: list[Port] | None = None
    depends_on: list[str] | None = None
    source: list[SourceRef] | None = None
    ai_context: AIContext | None = None
    provenance: Provenance | None = None
    tags: list[str] | None = None
    extensions: dict | None = None

    def port(self, name: str, direction: str = "outputs") -> Port | None:
        for p in getattr(self, direction) or ():
            if p.name == name:
                return p
        return None

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(id=d["id"], type=d["type"], name=d.get("name"),
                   description=d.get("description"), ref=d.get("ref"), config=d.get("config"),
                   inputs=_many(d, "inputs", Port), outputs=_many(d, "outputs", Port),
                   depends_on=d.get("depends_on"), source=_many(d, "source", SourceRef),
                   ai_context=_one(d, "ai_context", AIContext),
                   provenance=_one(d, "provenance", Provenance),
                   tags=d.get("tags"), extensions=d.get("extensions"))

    def to_dict(self) -> dict:
        return _pack(id=self.id, type=self.type, name=self.name, description=self.description,
                     ref=self.ref, config=self.config, inputs=_dump(self.inputs),
                     outputs=_dump(self.outputs), depends_on=self.depends_on,
                     source=_dump(self.source),
                     ai_context=self.ai_context.to_dict() if self.ai_context else None,
                     provenance=self.provenance.to_dict() if self.provenance else None,
                     tags=self.tags, extensions=self.extensions)


@dataclass
class Edge(_Base):
    """A typed semantic relationship. The type carries meaning, not decoration."""
    id: str
    type: str
    source: str
    target: str
    source_port: str | None = None
    target_port: str | None = None
    label: str | None = None
    when: str | None = None
    default: bool | None = None
    payload: dict | None = None
    optional: bool | None = None
    source_ref: list[SourceRef] | None = None
    ai_context: AIContext | None = None
    provenance: Provenance | None = None
    extensions: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(id=d["id"], type=d["type"], source=d["source"], target=d["target"],
                   source_port=d.get("source_port"), target_port=d.get("target_port"),
                   label=d.get("label"), when=d.get("when"), default=d.get("default"),
                   payload=d.get("payload"), optional=d.get("optional"),
                   source_ref=_many(d, "source_ref", SourceRef),
                   ai_context=_one(d, "ai_context", AIContext),
                   provenance=_one(d, "provenance", Provenance),
                   extensions=d.get("extensions"))

    def to_dict(self) -> dict:
        return _pack(id=self.id, type=self.type, source=self.source, target=self.target,
                     source_port=self.source_port, target_port=self.target_port,
                     label=self.label, when=self.when, default=self.default,
                     payload=self.payload, optional=self.optional,
                     source_ref=_dump(self.source_ref),
                     ai_context=self.ai_context.to_dict() if self.ai_context else None,
                     provenance=self.provenance.to_dict() if self.provenance else None,
                     extensions=self.extensions)


# ---------------------------------------------------------------------------
# registries -- reusable definitions, declared once and referenced by nodes
# ---------------------------------------------------------------------------
@dataclass
class Prompt(_Base):
    id: str
    name: str | None = None
    description: str | None = None
    role: str | None = None
    template: str | None = None
    template_format: str | None = None
    variables: list[dict] | None = None
    source: list[SourceRef] | None = None
    ai_context: AIContext | None = None
    provenance: Provenance | None = None
    extensions: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Prompt":
        return cls(id=d["id"], name=d.get("name"), description=d.get("description"),
                   role=d.get("role"), template=d.get("template"),
                   template_format=d.get("template_format"), variables=d.get("variables"),
                   source=_many(d, "source", SourceRef),
                   ai_context=_one(d, "ai_context", AIContext),
                   provenance=_one(d, "provenance", Provenance),
                   extensions=d.get("extensions"))

    def to_dict(self) -> dict:
        return _pack(id=self.id, name=self.name, description=self.description, role=self.role,
                     template=self.template, template_format=self.template_format,
                     variables=self.variables, source=_dump(self.source),
                     ai_context=self.ai_context.to_dict() if self.ai_context else None,
                     provenance=self.provenance.to_dict() if self.provenance else None,
                     extensions=self.extensions)


@dataclass
class ModelDef(_Base):
    id: str
    provider: str
    model: str
    parameters: dict | None = None
    structured_output: dict | None = None
    source: list[SourceRef] | None = None
    provenance: Provenance | None = None
    extensions: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ModelDef":
        return cls(id=d["id"], provider=d["provider"], model=d["model"],
                   parameters=d.get("parameters"), structured_output=d.get("structured_output"),
                   source=_many(d, "source", SourceRef),
                   provenance=_one(d, "provenance", Provenance),
                   extensions=d.get("extensions"))

    def to_dict(self) -> dict:
        return _pack(id=self.id, provider=self.provider, model=self.model,
                     parameters=self.parameters, structured_output=self.structured_output,
                     source=_dump(self.source),
                     provenance=self.provenance.to_dict() if self.provenance else None,
                     extensions=self.extensions)


@dataclass
class ToolDef(_Base):
    id: str
    name: str
    description: str | None = None
    kind: str | None = None
    parameters: dict | None = None
    returns: dict | None = None
    side_effects: bool | None = None
    idempotent: bool | None = None
    source: list[SourceRef] | None = None
    ai_context: AIContext | None = None
    provenance: Provenance | None = None
    extensions: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ToolDef":
        return cls(id=d["id"], name=d["name"], description=d.get("description"),
                   kind=d.get("kind"), parameters=d.get("parameters"), returns=d.get("returns"),
                   side_effects=d.get("side_effects"), idempotent=d.get("idempotent"),
                   source=_many(d, "source", SourceRef),
                   ai_context=_one(d, "ai_context", AIContext),
                   provenance=_one(d, "provenance", Provenance),
                   extensions=d.get("extensions"))

    def to_dict(self) -> dict:
        return _pack(id=self.id, name=self.name, description=self.description, kind=self.kind,
                     parameters=self.parameters, returns=self.returns,
                     side_effects=self.side_effects, idempotent=self.idempotent,
                     source=_dump(self.source),
                     ai_context=self.ai_context.to_dict() if self.ai_context else None,
                     provenance=self.provenance.to_dict() if self.provenance else None,
                     extensions=self.extensions)


@dataclass
class DataSource(_Base):
    id: str
    kind: str
    name: str | None = None
    description: str | None = None
    provider: str | None = None
    config: dict | None = None
    source: list[SourceRef] | None = None
    ai_context: AIContext | None = None
    provenance: Provenance | None = None
    extensions: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "DataSource":
        return cls(id=d["id"], kind=d["kind"], name=d.get("name"),
                   description=d.get("description"), provider=d.get("provider"),
                   config=d.get("config"), source=_many(d, "source", SourceRef),
                   ai_context=_one(d, "ai_context", AIContext),
                   provenance=_one(d, "provenance", Provenance),
                   extensions=d.get("extensions"))

    def to_dict(self) -> dict:
        return _pack(id=self.id, name=self.name, description=self.description, kind=self.kind,
                     provider=self.provider, config=self.config, source=_dump(self.source),
                     ai_context=self.ai_context.to_dict() if self.ai_context else None,
                     provenance=self.provenance.to_dict() if self.provenance else None,
                     extensions=self.extensions)


# ---------------------------------------------------------------------------
# document
# ---------------------------------------------------------------------------
@dataclass
class Framework(_Base):
    name: str
    version: str | None = None
    adapter: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Framework":
        return cls(name=d["name"], version=d.get("version"), adapter=d.get("adapter"))

    def to_dict(self) -> dict:
        return _pack(name=self.name, version=self.version, adapter=self.adapter)


@dataclass
class Project(_Base):
    name: str | None = None
    description: str | None = None
    version: str | None = None
    repository: str | None = None
    commit: str | None = None
    root: str | None = None
    languages: list[str] | None = None
    frameworks: list[Framework] | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(name=d.get("name"), description=d.get("description"),
                   version=d.get("version"), repository=d.get("repository"),
                   commit=d.get("commit"), root=d.get("root"), languages=d.get("languages"),
                   frameworks=_many(d, "frameworks", Framework))

    def to_dict(self) -> dict:
        return _pack(name=self.name, description=self.description, version=self.version,
                     repository=self.repository, commit=self.commit, root=self.root,
                     languages=self.languages, frameworks=_dump(self.frameworks))


@dataclass
class Document(_Base):
    """A complete `.aiflow` document."""
    format: str = "aiflow"
    version: str = "1.0"
    project: Project | None = None
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    prompts: list[Prompt] | None = None
    models: list[ModelDef] | None = None
    tools: list[ToolDef] | None = None
    data_sources: list[DataSource] | None = None
    metadata: dict | None = None
    provenance: Provenance | None = None
    extensions: dict | None = None

    # -- lookup ----------------------------------------------------------
    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def edge(self, edge_id: str) -> Edge | None:
        return next((e for e in self.edges if e.id == edge_id), None)

    def registry(self, key: str) -> list:
        return getattr(self, key) or []

    def resolve(self, node: Node):
        """Return the registry entry a node's `ref` points at, if any."""
        from .spec import REF_REGISTRY
        if node.ref is None:
            return None
        key = REF_REGISTRY.get(node.type)
        if key is None:
            return None
        return next((item for item in self.registry(key) if item.id == node.ref), None)

    # -- serialization ---------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        return cls(
            format=d.get("format", "aiflow"), version=d.get("version", "1.0"),
            project=_one(d, "project", Project),
            nodes=[Node.from_dict(n) for n in d.get("nodes", [])],
            edges=[Edge.from_dict(e) for e in d.get("edges", [])],
            prompts=_many(d, "prompts", Prompt), models=_many(d, "models", ModelDef),
            tools=_many(d, "tools", ToolDef),
            data_sources=_many(d, "data_sources", DataSource),
            metadata=d.get("metadata"), provenance=_one(d, "provenance", Provenance),
            extensions=d.get("extensions"),
        )

    def to_dict(self) -> dict:
        return _pack(
            format=self.format, version=self.version,
            project=self.project.to_dict() if self.project else None,
            nodes=[n.to_dict() for n in self.nodes],
            edges=[e.to_dict() for e in self.edges],
            prompts=_dump(self.prompts), models=_dump(self.models),
            tools=_dump(self.tools), data_sources=_dump(self.data_sources),
            metadata=self.metadata,
            provenance=self.provenance.to_dict() if self.provenance else None,
            extensions=self.extensions,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Document":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def save(self, path: str | Path, indent: int = 2) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=indent) + "\n")

    def __repr__(self) -> str:
        name = (self.project.name if self.project else None) or "<unnamed>"
        return (f"Document({name!r}, {len(self.nodes)} nodes, {len(self.edges)} edges, "
                f"v{self.version})")
