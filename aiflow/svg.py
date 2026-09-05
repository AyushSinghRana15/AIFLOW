"""Export a workflow to a standalone SVG.

Useful for embedding a workflow in a README, a pull request, or a design doc,
where the interactive HTML viewer cannot run. Styles are written as literal
attributes rather than CSS custom properties, because renderers that sanitize
SVG -- GitHub among them -- routinely strip `<style>` blocks. Two themes are
emitted instead, and callers pair them with `<picture>`.
"""
from __future__ import annotations

from pathlib import Path

from .graph import Graph
from .layout import compute_layout
from .model import Document

__all__ = ["render_svg", "render_svg_to_file", "THEMES"]

THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#ffffff", "ink": "#16181d", "muted": "#6b7280",
        "edge": "#9aa1ae", "warn": "#b45309",
        "agent": "#4c6ef5", "llm": "#7950f2", "tool": "#f08c00", "prompt": "#0ca678",
        "retriever": "#1098ad", "vector_store": "#1971c2", "input": "#2f9e44",
        "output": "#e03131", "condition": "#f76707",
    },
    "dark": {
        "bg": "#0e1014", "panel": "#171a21", "ink": "#e7e9ee", "muted": "#9aa1ae",
        "edge": "#6b7280", "warn": "#fcc419",
        "agent": "#748ffc", "llm": "#b197fc", "tool": "#ffc078", "prompt": "#38d9a9",
        "retriever": "#3bc9db", "vector_store": "#74c0fc", "input": "#69db7c",
        "output": "#ff8787", "condition": "#ffa94d",
    },
}

DASH = {"uses": "5 4", "routes_to": "2 4"}
WIDTH = {"calls": 2.0, "produces": 2.4}
FONT = ("ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif")


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_svg(doc: Document, *, theme: str = "light",
               orientation: str = "horizontal", show_legend: bool = True) -> str:
    if theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}; expected one of {sorted(THEMES)}")
    c = THEMES[theme]
    g = Graph(doc)
    layout = compute_layout(g, orientation=orientation)
    place = layout.placements

    unhandled = {f.subject for f in g.unhandled_failures()}
    inferred = {x.id for x in list(doc.nodes) + list(doc.edges)
                if getattr(x, "provenance", None) and x.provenance.is_inferred}

    used_types = sorted({n.type for n in doc.nodes})
    legend_h = 34 if (show_legend and used_types) else 0
    width, height = layout.width, layout.height + legend_h

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="{FONT}" role="img">',
        f'<title>{_esc((doc.project.name if doc.project else None) or "AIFLOW workflow")}</title>',
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="{c["bg"]}"/>',
        '<defs>',
        f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{c["edge"]}"/></marker>',
        '</defs>',
    ]

    # edges first so nodes paint over them
    for e in doc.edges:
        a, b = place.get(e.source), place.get(e.target)
        if not a or not b:
            continue
        x1, y1 = a.x + a.w, a.y + a.h / 2
        x2, y2 = b.x, b.y + b.h / 2
        dx = max(40, abs(x2 - x1) * 0.45)
        stroke = c["retriever"] if e.type == "retrieves" else c["edge"]
        attrs = [f'fill="none"', f'stroke="{stroke}"',
                 f'stroke-width="{WIDTH.get(e.type, 1.5)}"', 'opacity="0.65"',
                 'marker-end="url(#a)"']
        if e.type in DASH:
            attrs.append(f'stroke-dasharray="{DASH[e.type]}"')
        out.append(f'<path d="M{x1:.0f},{y1:.0f} C{x1 + dx:.0f},{y1:.0f} '
                   f'{x2 - dx:.0f},{y2:.0f} {x2:.0f},{y2:.0f}" {" ".join(attrs)}/>')

        label = e.label or (e.when if e.type == "routes_to" else None)
        if label:
            out.append(f'<text x="{(x1 + x2) / 2:.0f}" y="{(y1 + y2) / 2 - 6:.0f}" '
                       f'text-anchor="middle" font-size="9" fill="{c["muted"]}">'
                       f'{_esc(_trim(label, 22))}</text>')

    for n in doc.nodes:
        p = place.get(n.id)
        if not p:
            continue
        tint = c.get(n.type, c["muted"])
        out.append(f'<g transform="translate({p.x:.0f},{p.y:.0f})">')
        out.append(f'<rect width="{p.w:.0f}" height="{p.h:.0f}" rx="9" '
                   f'fill="{c["panel"]}" stroke="{tint}" stroke-width="1.6"/>')
        out.append(f'<text x="12" y="22" font-size="12" font-weight="600" '
                   f'fill="{c["ink"]}">{_esc(_trim(n.name or n.id, 22))}</text>')
        out.append(f'<text x="12" y="38" font-size="10" fill="{c["muted"]}">'
                   f'{_esc(n.type)}</text>')
        flags = ("▲" if n.id in unhandled else "") + ("◆" if n.id in inferred else "")
        if flags:
            out.append(f'<text x="{p.w - 10:.0f}" y="22" text-anchor="end" '
                       f'font-size="11" fill="{c["warn"]}">{flags}</text>')
        out.append('</g>')

    if legend_h:
        x, y = 48, layout.height + 6
        for t in used_types:
            out.append(f'<circle cx="{x + 4}" cy="{y + 8}" r="4" fill="{c.get(t, c["muted"])}"/>')
            out.append(f'<text x="{x + 14}" y="{y + 12}" font-size="10.5" '
                       f'fill="{c["muted"]}">{_esc(t)}</text>')
            x += 22 + len(t) * 6.2
    out.append('</svg>')
    return "\n".join(out) + "\n"


def render_svg_to_file(doc: Document, path: str | Path, *, theme: str = "light",
                       orientation: str = "horizontal") -> Path:
    path = Path(path)
    path.write_text(render_svg(doc, theme=theme, orientation=orientation))
    return path
