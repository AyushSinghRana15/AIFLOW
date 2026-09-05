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

from collections import defaultdict

from ..model import (DataSource, Document, Edge, ModelDef, Node, Project,
                     Prompt, Provenance, SourceRef, ToolDef, Framework)
from .python import Finding, FileReport, analyze_path

__all__ = ["assemble", "generate"]

GENERATOR = {"name": "aiflow-analyzer-python", "version": "0.1.0"}


def _adapter_index():
    from .. import adapters as registry
    return registry.ADAPTERS


class _LazyIndex:
    def __iter__(self):
        return iter(_adapter_index())


_ADAPTER_INDEX = _LazyIndex()

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


def _fw_prov(f: Finding, adapter: str, note: str | None = None) -> Provenance:
    """Provenance for a claim only a framework adapter could make.

    Distinct from `static_analysis` on purpose: a `routes_to` edge parsed from a
    real `add_conditional_edges` call is a firmer claim about branching than
    generic analysis can make, and a reader should be able to tell them apart.
    """
    return Provenance(method="framework_adapter", confidence=round(f.confidence, 2),
                      evidence=[_src(f)], generator={"name": adapter, "version": "0.1.0"},
                      notes=note)


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
def assemble(reports: list[FileReport], root: Path, *, name: str | None = None,
             adapters: list | None = None) -> Document:
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

    # -- topology contributed by a framework adapter ------------------------
    adapter_findings: list[Finding] = []
    adapter_names: list[str] = []
    adapter_notes: list[str] = []
    for result in (adapters or []):
        adapter_findings.extend(result.findings)
        adapter_notes.extend(result.notes)
        if result.findings:
            adapter_names.append(result.name)

    fw: dict[str, list[Finding]] = defaultdict(list)
    for f in adapter_findings:
        fw[f.kind].append(f)

    if fw["fw_node"]:
        adapter = adapter_names[0] if adapter_names else "adapter"
        # generic findings grouped by the function that contains them, so a graph
        # step can claim the LLM call, prompt and retrieval inside its handler
        by_function: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            if f.scope:
                by_function[f.scope.split(".")[0]].append(f)

        step: dict[str, str] = {}
        for f in fw["fw_node"]:
            label = f.data["label"]
            target = f.data.get("target") or label
            owned = by_function.get(target, [])
            has_llm = any(x.kind == "llm" for x in owned)
            has_retrieval = any(x.kind == "retrieval" for x in owned)
            node_type = "retriever" if has_retrieval and not has_llm else "agent"

            nid = ids.mint("", label)
            nodes.append(Node(id=nid, type=node_type, name=label,
                              source=[_src(f)],
                              provenance=_fw_prov(f, adapter,
                                                  f"declared as a graph step handled by "
                                                  f"{target!r}")))
            step[label] = nid

            for x in owned:
                if x.kind == "llm" and x.key in llm_node:
                    edge("calls", nid, llm_node[x.key], x,
                         "LLM invocation inside the step handler")
                    for pname in x.data.get("prompts", []):
                        if pname in prompt_node:
                            edge("uses", nid, prompt_node[pname], x,
                                 "prompt referenced by the step handler")
                if x.kind == "retrieval" and x.data.get("store") in store_node:
                    edge("retrieves", nid, store_node[x.data["store"]], x,
                         "retrieval call inside the step handler")

        # branches: the thing generic analysis cannot see
        for f in fw["fw_branch"]:
            source_label = f.data["source"]
            if source_label not in step:
                continue
            cid = ids.mint("cond_", f.data.get("router") or f"{source_label}_branch")
            nodes.append(Node(id=cid, type="condition", name=f.data.get("router") or "branch",
                              config={"router": f.data.get("router")} if f.data.get("router") else None,
                              source=[_src(f)],
                              provenance=_fw_prov(f, adapter, "declared via conditional edges")))
            edges.append(Edge(id=ids.mint("e_", f"{source_label}_to_{cid}"), type="passes",
                              source=step[source_label], target=cid,
                              provenance=_fw_prov(f, adapter)))
            for when, target_label in f.data.get("mapping", {}).items():
                if target_label in step:
                    edges.append(Edge(
                        id=ids.mint("e_", f"{cid}_{when}"), type="routes_to",
                        source=cid, target=step[target_label], when=f"route == {when!r}",
                        label=when, provenance=_fw_prov(f, adapter)))
            if f.data.get("partial"):
                nodes[-1].description = ("Some branch targets were not literal and are "
                                         "missing from this graph.")

        branch_sources = {f.data["source"] for f in fw["fw_branch"]}
        for f in fw["fw_edge"]:
            src, tgt = f.data["source"], f.data["target"]
            if src in step and tgt in step:
                edge("passes", step[src], step[tgt], f, "sequential graph edge")

        for f in fw["fw_entry"]:
            label = f.data["label"]
            if label not in step:
                continue
            in_id = ids.mint("in_", f"{label}_entry")
            nodes.append(Node(id=in_id, type="input", name="Graph entry",
                              source=[_src(f)], provenance=_fw_prov(f, adapter)))
            edge("passes", in_id, step[label], f, "graph entry point")

        for f in fw["fw_terminal"]:
            label = f.data["label"]
            if label not in step:
                continue
            out_id = ids.mint("out_", f"{label}_result")
            nodes.append(Node(id=out_id, type="output", name="Graph result",
                              source=[_src(f)], provenance=_fw_prov(f, adapter)))
            edges.append(Edge(id=ids.mint("e_", f"{label}_produces"), type="produces",
                              source=step[label], target=out_id,
                              provenance=_fw_prov(f, adapter)))

    # -- entrypoints: input, dataflow between components, output ------------
    # Skipped when an adapter already supplied the topology: the adapter parsed
    # the graph the framework actually builds, so inferring a second one from
    # call order would contradict it.
    instances = {f.name: f.data["class"] for f in by_kind.get("instance", [])}

    def component_for(receiver: str) -> str | None:
        cls = instances.get(receiver, receiver)
        return agent_node.get(cls) or retriever_node.get(cls)

    for f in ([] if fw["fw_node"] else by_kind.get("entrypoint", [])):
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
    handled = {a.framework: a.name for result in (adapters or [])
               for a in _ADAPTER_INDEX if a.name == result.name and result.findings}
    project = Project(
        name=name or root.resolve().name, root=".", languages=["python"],
        commit=_git_commit(root), repository=_git_remote(root),
        frameworks=[Framework(name=k, adapter=handled.get(k))
                    for k in sorted(frameworks)] or None,
    )

    notes = [n for r in reports for n in r.notes] + adapter_notes
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


def generate(path: str | Path, *, name: str | None = None,
             use_adapters: bool = True) -> tuple[Document, list[FileReport]]:
    """Analyze a project, then let any applicable framework adapter refine it."""
    root = Path(path)
    reports = analyze_path(root)
    results = []
    if use_adapters:
        from .. import adapters as adapter_registry
        results = adapter_registry.run(root, reports)
    return assemble(reports, root, name=name, adapters=results), reports
