"""Turn analyzer findings into an AIFLOW document.

Every element emitted here carries `static_analysis` provenance, the
confidence of the rule that matched, and a source reference pointing at the
code that justified it. Nothing is emitted that the syntax tree did not show:
no summaries, no intent, no failure modes. Those belong to the semantic
analyzer, and asserting them here would put guesses behind a label that means
"parsed".
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..model import (DataSource, Document, Edge, ModelDef, Node, Project,
                     Prompt, Provenance, SourceRef, ToolDef, Framework)
from .python import Finding, FileReport, analyze_path

__all__ = ["assemble", "generate"]

GENERATOR = {"name": "aiflow-analyzer-python", "version": "0.1.0"}

LIMITS = ("Structure extracted from the syntax tree only. Workflows assembled "
          "at runtime from configuration, and any branching semantics, are not "
          "visible to static analysis and are absent rather than guessed.")


def _snake(name: str) -> str:
    """CamelCase and ALL_CAPS both reduce to snake_case.

    Splitting on every capital would turn ROUTER_PROMPT into r_o_u_t_e_r_...,
    so the split happens only at a genuine case boundary.
    """
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "x"


class _Ids:
    def __init__(self):
        self.taken: set[str] = set()

    def mint(self, prefix: str, name: str) -> str:
        base = f"{prefix}{_snake(name)}"[:120]
        if not base[:1].isalpha():
            base = "n_" + base
        candidate, n = base, 2
        while candidate in self.taken:
            candidate = f"{base}_{n}"
            n += 1
        self.taken.add(candidate)
        return candidate


def _src(f: Finding) -> SourceRef:
    return SourceRef(file=f.file, start_line=f.line,
                     end_line=f.end_line if f.end_line != f.line else None,
                     symbol=f.symbol, language="python")


def _prov(f: Finding, note: str | None = None) -> Provenance:
    return Provenance(method="static_analysis", confidence=round(f.confidence, 2),
                      evidence=[_src(f)], generator=dict(GENERATOR), notes=note)


def _git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip()[:40] or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_remote(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5)
        url = out.stdout.strip()
        if out.returncode != 0 or not url:
            return None
        if url.startswith("git@github.com:"):
            url = "https://github.com/" + url.split(":", 1)[1]
        return url.removesuffix(".git")
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------
def assemble(reports: list[FileReport], root: Path, *, name: str | None = None) -> Document:
    findings: list[Finding] = [f for r in reports for f in r.findings]
    by_kind: dict[str, list[Finding]] = {}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)

    ids = _Ids()
    nodes: list[Node] = []
    edges: list[Edge] = []
    prompts: list[Prompt] = []
    models: list[ModelDef] = []
    tools: list[ToolDef] = []
    sources: list[DataSource] = []

    def edge(etype: str, src: str, tgt: str, f: Finding, note: str | None = None, **kw) -> None:
        edges.append(Edge(id=ids.mint("e_", f"{src}_{etype}_{tgt}"), type=etype,
                          source=src, target=tgt, provenance=_prov(f, note), **kw))

    # -- registries --------------------------------------------------------
    prompt_node: dict[str, str] = {}          # constant name -> node id
    for f in by_kind.get("prompt", []):
        pid = ids.mint("p_", f.name)
        prompts.append(Prompt(
            id=pid, name=f.name, template=f.data["template"], template_format="f-string",
            variables=[{"name": v} for v in f.data["variables"]] or None,
            source=[_src(f)], provenance=_prov(f)))
        nid = ids.mint("prompt_", f.name)
        nodes.append(Node(id=nid, type="prompt", ref=pid, name=f.name,
                          source=[_src(f)], provenance=_prov(f)))
        prompt_node[f.name] = nid

    model_by_const: dict[str, str] = {}
    model_by_literal: dict[str, str] = {}
    for f in by_kind.get("model", []):
        mid = ids.mint("m_", f.data["model"].replace(".", "_"))
        models.append(ModelDef(id=mid, provider=f.data["provider"], model=f.data["model"],
                               source=[_src(f)], provenance=_prov(f)))
        model_by_const[f.data["const"]] = mid
        model_by_literal[f.data["model"]] = mid

    tool_node: dict[str, str] = {}
    for f in by_kind.get("tool", []):
        if f.name in tool_node:
            continue
        tid = ids.mint("t_", f.name)
        tools.append(ToolDef(
            id=tid, name=f.name, description=f.data.get("description"),
            kind="function", parameters=f.data.get("schema"),
            source=[_src(f)], provenance=_prov(f)))
        nid = ids.mint("tool_", f.name)
        nodes.append(Node(id=nid, type="tool", ref=tid, name=f.name,
                          source=[_src(f)], provenance=_prov(f)))
        tool_node[f.name] = nid

    store_node: dict[str, str] = {}           # finding key -> node id
    for f in by_kind.get("vector_store", []):
        did = ids.mint("ds_", f.name)
        sources.append(DataSource(
            id=did, name=f.name, kind="vector_store", provider=f.data["provider"],
            config=f.data.get("config") or None, source=[_src(f)], provenance=_prov(f)))
        nid = ids.mint("vs_", f.name)
        nodes.append(Node(id=nid, type="vector_store", ref=did, name=f.name,
                          source=[_src(f)], provenance=_prov(f)))
        store_node[f.key] = nid

    # -- graph nodes -------------------------------------------------------
    agent_node: dict[str, str] = {}           # class name -> node id
    agent_finding: dict[str, Finding] = {}
    for f in by_kind.get("agent", []):
        nid = ids.mint("", f.name)
        nodes.append(Node(id=nid, type="agent", name=f.name,
                          description=f.data.get("docstring"),
                          source=[_src(f)], provenance=_prov(f, f.data["evidence"])))
        agent_node[f.name] = nid
        agent_finding[f.name] = f

    retriever_node: dict[str, str] = {}
    retriever_finding: dict[str, Finding] = {}
    for f in by_kind.get("retriever", []):
        nid = ids.mint("", f.name)
        nodes.append(Node(id=nid, type="retriever", name=f.name,
                          description=f.data.get("docstring"),
                          source=[_src(f)], provenance=_prov(f, f.data["evidence"])))
        retriever_node[f.name] = nid
        retriever_finding[f.name] = f

    llm_node: dict[str, str] = {}             # finding key -> node id
    for f in by_kind.get("llm", []):
        const, literal = f.data.get("model_const"), f.data.get("model")
        ref = model_by_const.get(const) or model_by_literal.get(literal or "")
        if ref is None:                       # a model the analyzer could not name
            ref = ids.mint("m_", f"{f.data['provider']}_unresolved")
            models.append(ModelDef(id=ref, provider=f.data["provider"], model="unknown",
                                   source=[_src(f)],
                                   provenance=_prov(f, "Model identifier is not a literal "
                                                       "or a resolvable constant.")))
        owner = f.scope.split(".")[0] if f.scope else f.file
        nid = ids.mint("llm_", owner)
        nodes.append(Node(id=nid, type="llm", ref=ref, name=f.name,
                          config=f.data.get("parameters") or None,
                          source=[_src(f)], provenance=_prov(f)))
        llm_node[f.key] = nid

    # -- edges: agent -> its own llm calls, prompts, tools, retrievers ------
    for cls, f in agent_finding.items():
        src = agent_node[cls]
        for key in f.data.get("llm_calls", []):
            if key in llm_node:
                edge("calls", src, llm_node[key], f, "LLM invocation inside the class body")
        refs = set(f.data.get("references", []))
        for pname, pnid in prompt_node.items():
            if pname in refs:
                edge("uses", src, pnid, f, "prompt constant referenced in the class body")
        for tname, tnid in tool_node.items():
            if tname in refs:
                edge("calls", src, tnid, f, "tool referenced in the class body")
        for rcls, rnid in retriever_node.items():
            if rcls in refs:
                edge("retrieves", src, rnid, f, "retriever constructed in the class body")

    # -- edges: retriever -> store ------------------------------------------
    for f in by_kind.get("retrieval", []):
        store = f.data.get("store")
        owner = f.scope.split(".")[0] if f.scope else None
        if store in store_node and owner in retriever_node:
            edge("retrieves", retriever_node[owner], store_node[store], f,
                 f"retrieval call on {f.data.get('receiver') or 'the store'}")

    # -- entrypoints: input, dataflow between components, output ------------
    instances = {f.name: f.data["class"] for f in by_kind.get("instance", [])}

    def component_for(receiver: str) -> str | None:
        cls = instances.get(receiver, receiver)
        return agent_node.get(cls) or retriever_node.get(cls)

    for f in by_kind.get("entrypoint", []):
        flow = [s for s in f.data.get("flow", []) if component_for(s["receiver"])]
        if not flow:
            continue
        in_id = ids.mint("in_", f.name)
        nodes.append(Node(id=in_id, type="input", name=f.name,
                          description=f"{f.data.get('kind', 'entry')} entrypoint"
                                      + (f" {f.data['route']}" if f.data.get("route") else ""),
                          source=[_src(f)], provenance=_prov(f)))
        edge("passes", in_id, component_for(flow[0]["receiver"]), f,
             "first component invoked by the entrypoint")

        for step in flow[1:]:
            target = component_for(step["receiver"])
            for upstream in step["consumes"]:
                src = component_for(upstream)
                if src and target and src != target:
                    edge("passes", src, target, f,
                         f"{step['receiver']}.{step['method']}() consumes a value "
                         f"produced by {upstream}")

        out_id = ids.mint("out_", f.name)
        nodes.append(Node(id=out_id, type="output", name=f"{f.name} response",
                          source=[_src(f)], provenance=_prov(f)))
        edge("produces", component_for(flow[-1]["receiver"]), out_id, f,
             "last component invoked before the entrypoint returns")

    # -- project ------------------------------------------------------------
    frameworks = {}
    for r in reports:
        frameworks.update(r.frameworks)
    project = Project(
        name=name or root.resolve().name, root=".", languages=["python"],
        commit=_git_commit(root), repository=_git_remote(root),
        frameworks=[Framework(name=k) for k in sorted(frameworks)] or None,
    )

    notes = [n for r in reports for n in r.notes]
    return Document(
        format="aiflow", version="1.0", project=project,
        nodes=nodes, edges=edges,
        prompts=prompts or None, models=models or None,
        tools=tools or None, data_sources=sources or None,
        metadata={"generator": dict(GENERATOR),
                  "files_analyzed": len(reports),
                  "skipped": notes or None},
        provenance=Provenance(method="static_analysis", generator=dict(GENERATOR),
                              notes=LIMITS),
    )


def generate(path: str | Path, *, name: str | None = None) -> tuple[Document, list[FileReport]]:
    root = Path(path)
    reports = analyze_path(root)
    return assemble(reports, root, name=name), reports
