"""Render a `.aiflow` document to a self-contained interactive HTML page.

The output is a single file with no external requests: the document, its
computed layout, and the derived analyses are embedded as JSON and driven by
vanilla JavaScript. That matters for a workflow viewer -- it has to open from
a file:// path, from a CI artifact, or from behind a proxy without a CDN.

Layout is computed here rather than read from the document, because the
specification treats layout as presentation and the semantic model as
authoritative.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .graph import Graph
from .layout import compute_layout
from .model import Document

__all__ = ["render_html", "render_to_file"]

_GITHUB = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


def _safe(url: str | None) -> str | None:
    """Only http(s) URLs may reach an href.

    A source ref's `url` comes from the document, and a document may come from
    somewhere untrusted -- a `javascript:` URI there would otherwise become a
    live link in the rendered page.
    """
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    return None


def _source_url(doc: Document, ref: dict) -> str | None:
    """Build a permalink for a source reference, pinned to a commit when known."""
    if ref.get("url"):
        return _safe(ref["url"])
    project = doc.project
    if not project or not project.repository:
        return None
    m = _GITHUB.match(project.repository)
    if not m:
        return None
    commit = ref.get("commit") or project.commit
    if not commit:
        return None
    owner, repo = m.groups()
    path = ref["file"]
    if project.root:
        path = f"{project.root.rstrip('/')}/{path}"
    url = f"https://github.com/{owner}/{repo}/blob/{commit}/{path}"
    if ref.get("start_line"):
        url += f"#L{ref['start_line']}"
        if ref.get("end_line") and ref["end_line"] != ref["start_line"]:
            url += f"-L{ref['end_line']}"
    return url


def _annotate_sources(doc: Document, payload: dict) -> None:
    """Attach resolved permalinks to every source ref in the payload."""
    def walk(obj):
        if isinstance(obj, dict):
            if "file" in obj and isinstance(obj.get("file"), str):
                link = _source_url(doc, obj)
                if link:
                    obj["_url"] = link
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(payload)


def build_payload(doc: Document, *, orientation: str = "horizontal") -> dict:
    g = Graph(doc)
    layout = compute_layout(g, orientation=orientation)

    document = doc.to_dict()
    _annotate_sources(doc, document)

    return {
        "document": document,
        "layout": layout.to_dict(),
        "analysis": {
            "summary": g.summary(),
            "paths": [{"nodes": list(p.nodes), "edges": list(p.edges)}
                      for p in g.paths_to_outputs()],
            "rag": [n.id for n in g.rag_components()],
            "agents_using_tools": {k: [t.id for t in v]
                                   for k, v in g.agents_using_tools().items()},
            "unhandled": [{"subject": f.subject, "detail": f.detail, "source": f.source}
                          for f in g.unhandled_failures()],
            "low_trust": [{"subject": f.subject, "detail": f.detail}
                          for f in g.low_trust()],
            "unreachable": [n.id for n in g.unreachable()],
        },
    }


def render_html(doc: Document, *, orientation: str = "horizontal") -> str:
    payload = build_payload(doc, orientation=orientation)
    title = (doc.project.name if doc.project else None) or "AIFLOW workflow"
    data = json.dumps(payload, separators=(",", ":"))
    # </script> inside embedded JSON would close the tag early
    data = data.replace("</", "<\\/")
    return (_TEMPLATE
            .replace("__AIFLOW_TITLE__", _escape(title))
            .replace('"__AIFLOW_DATA__"', data))


def render_to_file(doc: Document, path: str | Path, *, orientation: str = "horizontal") -> Path:
    path = Path(path)
    path.write_text(render_html(doc, orientation=orientation))
    return path


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__AIFLOW_TITLE__ &middot; AIFLOW</title>
<style>
:root {
  --bg: #fbfbfd; --panel: #ffffff; --ink: #16181d; --muted: #6b7280;
  --line: #e3e5ea; --accent: #3b5bdb; --shadow: 0 1px 2px rgba(16,18,29,.06), 0 8px 24px rgba(16,18,29,.06);
  --agent:#4c6ef5; --llm:#7950f2; --tool:#f08c00; --prompt:#0ca678; --retriever:#1098ad;
  --vector_store:#1971c2; --input:#2f9e44; --output:#e03131; --condition:#f76707;
  --warn:#b45309; --warn-bg:#fef3c7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0e1014; --panel:#171a21; --ink:#e7e9ee; --muted:#9aa1ae;
    --line:#262a33; --accent:#748ffc; --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
    --agent:#748ffc; --llm:#b197fc; --tool:#ffc078; --prompt:#38d9a9; --retriever:#3bc9db;
    --vector_store:#74c0fc; --input:#69db7c; --output:#ff8787; --condition:#ffa94d;
    --warn:#fcc419; --warn-bg:#3a2f0b;
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg); color: var(--ink); overflow: hidden;
}
code, .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }

#app { display: grid; grid-template-columns: 264px 1fr 340px; grid-template-rows: auto 1fr; height: 100%; }
header {
  grid-column: 1 / -1; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  padding: 12px 18px; border-bottom: 1px solid var(--line); background: var(--panel);
}
header h1 { font-size: 15px; margin: 0; font-weight: 650; }
header .meta { color: var(--muted); font-size: 12.5px; }
header .spacer { flex: 1; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  font-size: 11.5px; padding: 3px 9px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--muted);
  user-select: none; font-family: inherit;
}
.chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn {
  font-size: 12px; padding: 5px 11px; border-radius: 7px; cursor: pointer;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink); font-family: inherit;
}
.btn:hover { border-color: var(--accent); }

aside, #details {
  background: var(--panel); overflow-y: auto; padding: 14px;
  border-right: 1px solid var(--line);
}
#details { border-right: 0; border-left: 1px solid var(--line); }
.sec { font-size: 11px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted);
       margin: 16px 0 7px; font-weight: 650; }
.sec:first-child { margin-top: 0; }
#search {
  width: 100%; padding: 7px 10px; border-radius: 7px; border: 1px solid var(--line);
  background: var(--bg); color: var(--ink); font: inherit; font-size: 13px;
}
.item {
  display: flex; align-items: center; gap: 8px; padding: 5px 7px; border-radius: 6px;
  cursor: pointer; font-size: 13px;
}
.item:hover { background: var(--bg); }
.item[aria-selected="true"] { background: var(--accent); color: #fff; }
.item[aria-selected="true"] .sub { color: rgba(255,255,255,.8); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.item .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sub { color: var(--muted); font-size: 11.5px; }
.badge {
  display: inline-block; font-size: 10.5px; padding: 1px 6px; border-radius: 5px;
  background: var(--warn-bg); color: var(--warn); font-weight: 600; margin-left: auto; flex: none;
}

#canvas-wrap { position: relative; overflow: hidden; background: var(--bg); }
svg { width: 100%; height: 100%; display: block; cursor: grab; }
svg.panning { cursor: grabbing; }
.edge { fill: none; stroke: var(--muted); stroke-width: 1.5; opacity: .55; }
.edge.calls { stroke-width: 2; }
.edge.uses { stroke-dasharray: 5 4; }
.edge.routes_to { stroke-dasharray: 2 4; }
.edge.retrieves { stroke: var(--retriever); }
.edge.produces { stroke-width: 2.4; }
.edge.hl { opacity: 1; stroke: var(--accent); stroke-width: 3; }
.edge.dim { opacity: .1; }
.nodeg { cursor: pointer; }
.nodeg rect { fill: var(--panel); stroke-width: 1.6; }
.nodeg .title { font-size: 12.5px; font-weight: 600; fill: var(--ink); }
.nodeg .kind { font-size: 10.5px; fill: var(--muted); }
.nodeg.dim { opacity: .18; }
.nodeg.sel rect { stroke-width: 3; }
.nodeg .flag { font-size: 13px; }
.elabel { font-size: 10px; fill: var(--muted); }
#empty-paths { color: var(--muted); font-size: 12.5px; }

#details h2 { font-size: 15px; margin: 0 0 2px; }
.kv { display: grid; grid-template-columns: 92px 1fr; gap: 4px 10px; font-size: 12.5px; margin: 8px 0; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; overflow-wrap: anywhere; }
.note { font-size: 12.5px; color: var(--muted); margin: 6px 0; }
.warnbox {
  background: var(--warn-bg); color: var(--warn); border-radius: 7px;
  padding: 8px 10px; font-size: 12.5px; margin: 6px 0;
}
a { color: var(--accent); }
.pill {
  display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted); margin: 0 4px 4px 0;
}
.edgeline { font-size: 12px; padding: 3px 0; border-bottom: 1px solid var(--line); cursor: pointer; }
.edgeline:hover { color: var(--accent); }
.legend { display: flex; flex-wrap: wrap; gap: 5px; }
@media (max-width: 1100px) {
  #app { grid-template-columns: 1fr; grid-template-rows: auto auto 1fr auto; }
  aside, #details { max-height: 220px; border: 0; border-top: 1px solid var(--line); }
}
</style>
</head>
<body>
<div id="app">
  <header>
    <h1 id="title"></h1>
    <span class="meta" id="stats"></span>
    <span class="spacer"></span>
    <div class="chips" id="filters"></div>
    <select class="btn" id="pathsel" title="Highlight a path from input to output"></select>
    <button class="btn" id="fit">Fit</button>
  </header>

  <aside>
    <input id="search" placeholder="Filter components…" autocomplete="off">
    <div id="explorer"></div>
  </aside>

  <div id="canvas-wrap">
    <svg id="svg">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="context-stroke"></path>
        </marker>
      </defs>
      <g id="viewport">
        <g id="edges"></g>
        <g id="nodes"></g>
      </g>
    </svg>
  </div>

  <div id="details"></div>
</div>

<script id="aiflow-data" type="application/json">"__AIFLOW_DATA__"</script>
<script>
(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("aiflow-data").textContent);
  const doc = DATA.document, layout = DATA.layout, analysis = DATA.analysis;
  const nodes = doc.nodes || [], edges = doc.edges || [];
  const byId = new Map(nodes.map(n => [n.id, n]));
  const edgeById = new Map(edges.map(e => [e.id, e]));
  const REG = { llm: "models", tool: "tools", prompt: "prompts", vector_store: "data_sources",
                retriever: "data_sources" };
  const EDGE_TYPES = ["calls", "uses", "retrieves", "passes", "produces", "routes_to"];

  const unhandledBy = new Map();
  (analysis.unhandled || []).forEach(u => {
    if (!unhandledBy.has(u.subject)) unhandledBy.set(u.subject, []);
    unhandledBy.get(u.subject).push(u);
  });
  const inferredIds = new Set();
  nodes.concat(edges).forEach(x => {
    if (x.provenance && x.provenance.method === "ai_inference") inferredIds.add(x.id);
  });

  const el = id => document.getElementById(id);
  const svg = el("svg"), viewport = el("viewport");
  const state = { selected: null, hidden: new Set(), query: "", path: null };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function color(type) { return `var(--${type})`; }
  function resolve(node) {
    const key = REG[node.type];
    if (!key || !node.ref || !doc[key]) return null;
    return doc[key].find(x => x.id === node.ref) || null;
  }

  /* ---------- header ---------------------------------------------------- */
  el("title").textContent = (doc.project && doc.project.name) || "AIFLOW workflow";
  const s = analysis.summary;
  el("stats").textContent =
    `${s.nodes} nodes · ${s.edges} edges · ${s.paths_to_output} path(s) to output` +
    (s.inferred_elements ? ` · ${s.inferred_elements} inferred` : "");

  const filters = el("filters");
  EDGE_TYPES.forEach(t => {
    const b = document.createElement("button");
    b.className = "chip"; b.textContent = t; b.setAttribute("aria-pressed", "true");
    b.onclick = () => {
      state.hidden.has(t) ? state.hidden.delete(t) : state.hidden.add(t);
      b.setAttribute("aria-pressed", String(!state.hidden.has(t)));
      draw();
    };
    filters.appendChild(b);
  });

  const psel = el("pathsel");
  psel.innerHTML = '<option value="">No path highlighted</option>' +
    (analysis.paths || []).map((p, i) =>
      `<option value="${i}">Path ${i + 1} · via ${esc(p.nodes[4] || p.nodes[1] || "")}</option>`).join("");
  psel.onchange = () => {
    state.path = psel.value === "" ? null : analysis.paths[+psel.value];
    draw();
  };

  /* ---------- explorer -------------------------------------------------- */
  function buildExplorer() {
    const wrap = el("explorer");
    wrap.innerHTML = "";
    const q = state.query.toLowerCase();
    const groups = {};
    nodes.forEach(n => (groups[n.type] = groups[n.type] || []).push(n));

    Object.keys(groups).sort().forEach(type => {
      const matches = groups[type].filter(n =>
        !q || n.id.toLowerCase().includes(q) || (n.name || "").toLowerCase().includes(q));
      if (!matches.length) return;
      const h = document.createElement("div");
      h.className = "sec"; h.textContent = `${type} (${matches.length})`;
      wrap.appendChild(h);
      matches.forEach(n => {
        const row = document.createElement("div");
        row.className = "item";
        row.setAttribute("aria-selected", String(state.selected === n.id));
        row.innerHTML =
          `<span class="dot" style="background:${color(n.type)}"></span>` +
          `<span class="label">${esc(n.name || n.id)}</span>` +
          (unhandledBy.has(n.id) ? '<span class="badge">risk</span>' : "") +
          (inferredIds.has(n.id) ? '<span class="badge">inferred</span>' : "");
        row.onclick = () => select(n.id);
        wrap.appendChild(row);
      });
    });

    [["prompts", "prompt"], ["models", "llm"], ["tools", "tool"],
     ["data_sources", "vector_store"]].forEach(([key, tint]) => {
      const items = (doc[key] || []).filter(x =>
        !q || x.id.toLowerCase().includes(q) || (x.name || "").toLowerCase().includes(q));
      if (!items.length) return;
      const h = document.createElement("div");
      h.className = "sec"; h.textContent = `${key.replace("_", " ")} · registry`;
      wrap.appendChild(h);
      items.forEach(x => {
        const row = document.createElement("div");
        row.className = "item";
        const users = nodes.filter(n => n.ref === x.id).length;
        row.innerHTML =
          `<span class="dot" style="background:${color(tint)};opacity:.5"></span>` +
          `<span class="label">${esc(x.name || x.id)}</span>` +
          `<span class="sub">${users}×</span>`;
        row.onclick = () => {
          const first = nodes.find(n => n.ref === x.id);
          if (first) select(first.id); else showRegistry(key, x);
        };
        wrap.appendChild(row);
      });
    });
  }

  el("search").oninput = e => { state.query = e.target.value; buildExplorer(); draw(); };

  /* ---------- graph ------------------------------------------------------ */
  function visibleEdges() { return edges.filter(e => !state.hidden.has(e.type)); }

  function matchesQuery(n) {
    if (!state.query) return true;
    const q = state.query.toLowerCase();
    return n.id.toLowerCase().includes(q) || (n.name || "").toLowerCase().includes(q);
  }

  function draw() {
    const L = layout.nodes;
    const pathNodes = state.path ? new Set(state.path.nodes) : null;
    const pathEdges = state.path ? new Set(state.path.edges) : null;

    let neighbours = null;
    if (state.selected) {
      neighbours = new Set([state.selected]);
      edges.forEach(e => {
        if (e.source === state.selected) neighbours.add(e.target);
        if (e.target === state.selected) neighbours.add(e.source);
      });
    }

    const eg = el("edges"); eg.innerHTML = "";
    visibleEdges().forEach(e => {
      const a = L[e.source], b = L[e.target];
      if (!a || !b) return;
      const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
      const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`);
      p.setAttribute("marker-end", "url(#arrow)");
      let cls = "edge " + e.type;
      if (pathEdges) cls += pathEdges.has(e.id) ? " hl" : " dim";
      else if (neighbours && !(e.source === state.selected || e.target === state.selected)) cls += " dim";
      p.setAttribute("class", cls);
      p.appendChild(title(`${e.source} --${e.type}--> ${e.target}` + (e.when ? `\nwhen ${e.when}` : "")));
      eg.appendChild(p);

      if (e.type === "routes_to" && (e.label || e.when)) {
        const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
        t.setAttribute("class", "elabel");
        t.setAttribute("x", (x1 + x2) / 2); t.setAttribute("y", (y1 + y2) / 2 - 5);
        t.setAttribute("text-anchor", "middle");
        t.textContent = e.label || e.when;
        eg.appendChild(t);
      }
    });

    const ng = el("nodes"); ng.innerHTML = "";
    nodes.forEach(n => {
      const p = L[n.id]; if (!p) return;
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      let cls = "nodeg";
      if (state.selected === n.id) cls += " sel";
      if (pathNodes && !pathNodes.has(n.id)) cls += " dim";
      else if (!pathNodes && neighbours && !neighbours.has(n.id)) cls += " dim";
      else if (!matchesQuery(n)) cls += " dim";
      g.setAttribute("class", cls);
      g.setAttribute("transform", `translate(${p.x},${p.y})`);
      g.onclick = ev => { ev.stopPropagation(); select(n.id); };

      const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      r.setAttribute("width", p.w); r.setAttribute("height", p.h);
      r.setAttribute("rx", 9); r.setAttribute("stroke", color(n.type));
      g.appendChild(r);

      const t1 = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t1.setAttribute("class", "title"); t1.setAttribute("x", 12); t1.setAttribute("y", 23);
      t1.textContent = trim(n.name || n.id, 22);
      g.appendChild(t1);

      const t2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t2.setAttribute("class", "kind"); t2.setAttribute("x", 12); t2.setAttribute("y", 40);
      t2.textContent = n.type;
      g.appendChild(t2);

      const flags = (unhandledBy.has(n.id) ? "▲" : "") + (inferredIds.has(n.id) ? "◆" : "");
      if (flags) {
        const f = document.createElementNS("http://www.w3.org/2000/svg", "text");
        f.setAttribute("class", "flag"); f.setAttribute("x", p.w - 12);
        f.setAttribute("y", 24); f.setAttribute("text-anchor", "end");
        f.setAttribute("fill", "var(--warn)");
        f.textContent = flags;
        g.appendChild(f);
      }
      g.appendChild(title(`${n.id} (${n.type})`));
      ng.appendChild(g);
    });
  }

  function title(text) {
    const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
    t.textContent = text; return t;
  }
  function trim(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  /* ---------- details ---------------------------------------------------- */
  function sourceLine(sr) {
    let loc = sr.file;
    if (sr.start_line) {
      loc += ":" + sr.start_line;
      if (sr.end_line && sr.end_line !== sr.start_line) loc += "-" + sr.end_line;
    }
    const text = esc(loc) + (sr.symbol ? ` <span class="sub">${esc(sr.symbol)}</span>` : "");
    return sr._url
      ? `<div><a class="mono" href="${esc(sr._url)}" target="_blank" rel="noopener">${text}</a></div>`
      : `<div class="mono">${text}</div>`;
  }

  function showRegistry(key, entry) {
    el("details").innerHTML =
      `<h2>${esc(entry.name || entry.id)}</h2><div class="sub">${esc(key)} registry</div>` +
      `<dl class="kv">` +
      Object.entries(entry).filter(([k]) => !["id", "name", "source", "provenance",
        "ai_context", "extensions"].includes(k))
        .map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono">${esc(
          typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("") +
      `</dl>` +
      (entry.source || []).map(sourceLine).join("");
  }

  function select(id) {
    state.selected = id;
    const n = byId.get(id);
    const d = el("details");
    if (!n) { d.innerHTML = ""; return; }

    const entry = resolve(n);
    const ins = edges.filter(e => e.target === id), outs = edges.filter(e => e.source === id);
    const risks = unhandledBy.get(id) || [];
    const ctx = n.ai_context || {};

    d.innerHTML =
      `<h2>${esc(n.name || n.id)}</h2>` +
      `<div class="sub mono">${esc(n.id)} · ${esc(n.type)}</div>` +
      (n.description ? `<p class="note">${esc(n.description)}</p>` : "") +
      (ctx.summary ? `<p class="note">${esc(ctx.summary)}</p>` : "") +
      (ctx.intent ? `<p class="note"><em>Why:</em> ${esc(ctx.intent)}</p>` : "") +

      (risks.length ? `<div class="sec">Unhandled failures</div>` +
        risks.map(r => `<div class="warnbox">▲ ${esc(r.detail)}</div>`).join("") : "") +

      (entry ? `<div class="sec">Definition</div>` +
        `<div class="note mono">${esc(entry.id)}` +
        (entry.model ? ` · ${esc(entry.provider)}/${esc(entry.model)}` : "") +
        (entry.provider && !entry.model ? ` · ${esc(entry.provider)}` : "") +
        `</div>` +
        (entry.template ? `<pre class="note mono" style="white-space:pre-wrap">${esc(
          trim(entry.template, 400))}</pre>` : "") : "") +

      ((n.inputs || []).length || (n.outputs || []).length ?
        `<div class="sec">Ports</div>` +
        (n.inputs || []).map(p => `<span class="pill">in ${esc(p.name)}${
          p.type ? ": " + esc(p.type) : ""}</span>`).join("") +
        (n.outputs || []).map(p => `<span class="pill">out ${esc(p.name)}${
          p.type ? ": " + esc(p.type) : ""}</span>`).join("") : "") +

      (n.provenance ? `<div class="sec">Provenance</div><dl class="kv">` +
        `<dt>method</dt><dd>${esc(n.provenance.method)}</dd>` +
        (n.provenance.confidence != null ?
          `<dt>confidence</dt><dd>${n.provenance.confidence}</dd>` : "") +
        (n.provenance.notes ? `<dt>notes</dt><dd>${esc(n.provenance.notes)}</dd>` : "") +
        `</dl>` : "") +

      ((n.source || []).length ? `<div class="sec">Source</div>` +
        n.source.map(sourceLine).join("") : "") +

      (ctx.invariants && ctx.invariants.length ? `<div class="sec">Invariants</div>` +
        ctx.invariants.map(i => `<div class="note">· ${esc(i)}</div>`).join("") : "") +
      (ctx.side_effects && ctx.side_effects.length ? `<div class="sec">Side effects</div>` +
        ctx.side_effects.map(i => `<div class="note">· ${esc(i)}</div>`).join("") : "") +

      (ins.length ? `<div class="sec">Incoming</div>` + ins.map(e =>
        `<div class="edgeline" data-go="${esc(e.source)}">${esc(e.source)} <span class="sub">--${
          esc(e.type)}--></span></div>`).join("") : "") +
      (outs.length ? `<div class="sec">Outgoing</div>` + outs.map(e =>
        `<div class="edgeline" data-go="${esc(e.target)}"><span class="sub">--${
          esc(e.type)}--></span> ${esc(e.target)}${
          e.when ? ` <span class="sub">when ${esc(e.when)}</span>` : ""}</div>`).join("") : "");

    d.querySelectorAll("[data-go]").forEach(x =>
      x.onclick = () => select(x.getAttribute("data-go")));

    buildExplorer();
    draw();
  }

  /* ---------- pan / zoom -------------------------------------------------- */
  let vx = 0, vy = 0, scale = 1;
  function apply() { viewport.setAttribute("transform", `translate(${vx},${vy}) scale(${scale})`); }

  // A workflow is typically deep and narrow, so fitting the whole graph into a
  // landscape pane can shrink it past readability. Below MIN_FIT we stop
  // scaling down, anchor to the left, and let the user pan instead.
  const MIN_FIT = 0.55;
  function fit() {
    const r = svg.getBoundingClientRect();
    const pad = 40;
    const ideal = Math.min((r.width - pad) / layout.width,
                           (r.height - pad) / layout.height, 1.4);
    scale = Math.max(ideal, MIN_FIT);
    const w = layout.width * scale, h = layout.height * scale;
    vx = w <= r.width ? (r.width - w) / 2 : pad / 2;
    vy = h <= r.height ? (r.height - h) / 2 : pad / 2;
    apply();
  }
  el("fit").onclick = fit;

  let dragging = false, lx = 0, ly = 0;
  svg.addEventListener("pointerdown", e => {
    dragging = true; lx = e.clientX; ly = e.clientY;
    svg.classList.add("panning"); svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", e => {
    if (!dragging) return;
    vx += e.clientX - lx; vy += e.clientY - ly; lx = e.clientX; ly = e.clientY; apply();
  });
  svg.addEventListener("pointerup", e => {
    dragging = false; svg.classList.remove("panning");
    try { svg.releasePointerCapture(e.pointerId); } catch (_) {}
  });
  svg.addEventListener("wheel", e => {
    e.preventDefault();
    const r = svg.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const k = Math.exp(-e.deltaY * 0.0016);
    const next = Math.min(3, Math.max(0.15, scale * k));
    vx = mx - (mx - vx) * (next / scale);
    vy = my - (my - vy) * (next / scale);
    scale = next; apply();
  }, { passive: false });

  svg.addEventListener("click", e => {
    if (e.target === svg || e.target === viewport) {
      state.selected = null; el("details").innerHTML = intro(); buildExplorer(); draw();
    }
  });
  addEventListener("keydown", e => {
    if (e.key === "Escape") { state.selected = null; el("details").innerHTML = intro();
      buildExplorer(); draw(); }
    if (e.key === "f" && !/input|textarea/i.test(e.target.tagName)) fit();
  });

  function intro() {
    const a = analysis;
    return `<h2>Workflow</h2>` +
      (doc.project && doc.project.description ?
        `<p class="note">${esc(doc.project.description)}</p>` : "") +
      (a.unhandled.length ? `<div class="sec">Missing error handling</div>` +
        a.unhandled.map(u => `<div class="warnbox">▲ <strong>${esc(u.subject)}</strong><br>${
          esc(u.detail)}</div>`).join("") : "") +
      (a.low_trust.length ? `<div class="sec">Low-trust claims</div>` +
        a.low_trust.map(t => `<div class="warnbox">◆ ${esc(t.subject)} — ${esc(t.detail)}</div>`
        ).join("") : "") +
      (a.rag.length ? `<div class="sec">Retrieval surface</div>` +
        a.rag.map(r => `<span class="pill">${esc(r)}</span>`).join("") : "") +
      (Object.keys(a.agents_using_tools).length ? `<div class="sec">Agents using tools</div>` +
        Object.entries(a.agents_using_tools).map(([k, v]) =>
          `<div class="note"><strong>${esc(k)}</strong> → ${esc(v.join(", "))}</div>`).join("") : "") +
      (a.unreachable.length ? `<div class="sec">Unreachable</div>` +
        a.unreachable.map(r => `<span class="pill">${esc(r)}</span>`).join("") : "") +
      `<div class="sec">Legend</div><div class="legend">` +
      [...new Set(nodes.map(n => n.type))].sort().map(t =>
        `<span class="pill"><span class="dot" style="display:inline-block;background:${
          color(t)}"></span> ${esc(t)}</span>`).join("") + `</div>` +
      `<div class="note" style="margin-top:12px">Scroll to zoom · drag to pan · ` +
      `<kbd>f</kbd> to fit · <kbd>Esc</kbd> to deselect</div>`;
  }

  buildExplorer();
  el("details").innerHTML = intro();
  draw();
  requestAnimationFrame(fit);
  addEventListener("resize", fit);
})();
</script>
</body>
</html>
"""
