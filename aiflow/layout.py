"""Deterministic layered layout for AIFLOW graphs.

The specification is explicit that layout is presentation and the semantic
model is authoritative, so coordinates are never stored in the document --
they are derived here at render time. Keeping the algorithm in Python (rather
than in the renderer's JavaScript) makes it deterministic and testable.

This is a reduced Sugiyama pipeline:

  1. rank    -- longest-path layering over the acyclic subgraph
  2. order   -- barycenter sweeps to reduce edge crossings
  3. place   -- assign coordinates from rank and order

Cycles are broken by ignoring back edges during ranking; the edges are still
drawn, they simply do not constrain the layering.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .graph import Graph

__all__ = ["Layout", "Placement", "compute_layout"]

NODE_W = 172
NODE_H = 54
LAYER_GAP = 88
ROW_GAP = 30
MARGIN = 48
SWEEPS = 6


@dataclass(frozen=True)
class Placement:
    id: str
    x: float
    y: float
    w: float = NODE_W
    h: float = NODE_H
    layer: int = 0
    order: int = 0


@dataclass
class Layout:
    placements: dict[str, Placement]
    width: float
    height: float
    layers: list[list[str]]
    back_edges: set[str]

    def to_dict(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "layers": self.layers,
            "back_edges": sorted(self.back_edges),
            "nodes": {p.id: {"x": p.x, "y": p.y, "w": p.w, "h": p.h,
                             "layer": p.layer, "order": p.order}
                      for p in self.placements.values()},
        }


def _find_back_edges(g: Graph) -> set[str]:
    """Edge ids that close a cycle, found by DFS with a colour marking.

    Iterative, so a deeply nested workflow cannot blow the stack.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n.id: WHITE for n in g.doc.nodes}
    back: set[str] = set()

    for root in list(colour):
        if colour[root] != WHITE:
            continue
        stack = [(root, iter(g.out_edges(root)))]
        colour[root] = GREY
        while stack:
            node, edges = stack[-1]
            advanced = False
            for e in edges:
                if e.target not in colour:
                    continue
                state = colour[e.target]
                if state == GREY:
                    back.add(e.id)
                elif state == WHITE:
                    colour[e.target] = GREY
                    stack.append((e.target, iter(g.out_edges(e.target))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
    return back


def _rank(g: Graph, back: set[str]) -> dict[str, int]:
    """Longest-path layering: a node sits one layer past its deepest predecessor."""
    indeg: dict[str, int] = defaultdict(int)
    forward: dict[str, list] = defaultdict(list)
    for e in g.doc.edges:
        if e.id in back or g.doc.node(e.source) is None or g.doc.node(e.target) is None:
            continue
        forward[e.source].append(e.target)
        indeg[e.target] += 1

    rank = {n.id: 0 for n in g.doc.nodes}
    queue = [n.id for n in g.doc.nodes if indeg[n.id] == 0]
    processed = 0
    while queue:
        node = queue.pop(0)
        processed += 1
        for nxt in forward[node]:
            rank[nxt] = max(rank[nxt], rank[node] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)

    # Any node left with a non-zero in-degree sits in a cycle the back-edge pass
    # did not fully break; park it after its deepest ranked predecessor.
    if processed < len(rank):
        for e in g.doc.edges:
            if e.source in rank and e.target in rank:
                rank[e.target] = max(rank[e.target], rank[e.source] + 1)
    return rank


def _order(g: Graph, layers: list[list[str]]) -> list[list[str]]:
    """Barycenter sweeps: repeatedly reposition each layer at the average
    position of its neighbours in the adjacent layer."""
    pos = {nid: i for layer in layers for i, nid in enumerate(layer)}

    def barycenter(nid: str, neighbours: list[str]) -> float:
        known = [pos[n] for n in neighbours if n in pos]
        return sum(known) / len(known) if known else pos[nid]

    for sweep in range(SWEEPS):
        downward = sweep % 2 == 0
        indices = range(1, len(layers)) if downward else range(len(layers) - 2, -1, -1)
        for i in indices:
            layer = layers[i]
            neighbours = {
                nid: [e.source for e in g.in_edges(nid)] if downward
                else [e.target for e in g.out_edges(nid)]
                for nid in layer
            }
            # sort is stable, so ties keep their previous order and the
            # result is deterministic across runs
            layer.sort(key=lambda nid: barycenter(nid, neighbours[nid]))
            for j, nid in enumerate(layer):
                pos[nid] = j
    return layers


def compute_layout(g: Graph, *, orientation: str = "horizontal") -> Layout:
    """Place every node. Horizontal runs input -> output left to right."""
    back = _find_back_edges(g)
    rank = _rank(g, back)

    buckets: dict[int, list[str]] = defaultdict(list)
    for n in g.doc.nodes:                      # document order seeds the sweep
        buckets[rank[n.id]].append(n.id)
    layers = _order(g, [buckets[k] for k in sorted(buckets)])

    tallest = max((len(layer) for layer in layers), default=1)
    span = tallest * NODE_H + (tallest - 1) * ROW_GAP

    placements: dict[str, Placement] = {}
    for li, layer in enumerate(layers):
        extent = len(layer) * NODE_H + (len(layer) - 1) * ROW_GAP
        offset = (span - extent) / 2
        for oi, nid in enumerate(layer):
            along = MARGIN + li * (NODE_W + LAYER_GAP)
            across = MARGIN + offset + oi * (NODE_H + ROW_GAP)
            x, y = (along, across) if orientation == "horizontal" else (across, along)
            placements[nid] = Placement(nid, x, y, layer=li, order=oi)

    if orientation == "horizontal":
        width = MARGIN * 2 + len(layers) * NODE_W + max(len(layers) - 1, 0) * LAYER_GAP
        height = MARGIN * 2 + span
    else:
        width = MARGIN * 2 + span
        height = MARGIN * 2 + len(layers) * NODE_H + max(len(layers) - 1, 0) * LAYER_GAP

    return Layout(placements, width, height, layers, back)
