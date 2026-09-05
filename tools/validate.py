#!/usr/bin/env python3
"""AIFLOW v1 validator.

Two levels:
  L1 structural  -- JSON Schema (spec/aiflow-v1.schema.json)
  L2 semantic    -- referential integrity, edge/node type compatibility,
                    port bindings, reachability, provenance discipline.

JSON Schema cannot dereference ids to their node types, so every
cross-reference rule lives in L2.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"
SCHEMA_PATH = SPEC_DIR / "aiflow-v1.schema.json"
MATRIX_PATH = SPEC_DIR / "edge-compatibility.json"

# node type -> registry key its `ref` must resolve against
REF_REGISTRY = {
    "llm": "models",
    "tool": "tools",
    "prompt": "prompts",
    "vector_store": "data_sources",
    "retriever": "data_sources",
}

LOW_CONFIDENCE = 0.60


class Finding:
    __slots__ = ("code", "severity", "path", "message")

    def __init__(self, code, severity, path, message):
        self.code, self.severity, self.path, self.message = code, severity, path, message

    def __str__(self):
        return f"{self.severity.upper():7} {self.code}  {self.path}\n          {self.message}"

    def as_dict(self):
        return {"code": self.code, "severity": self.severity,
                "path": self.path, "message": self.message}


class Report:
    def __init__(self):
        self.findings: list[Finding] = []

    def error(self, code, path, msg):
        self.findings.append(Finding(code, "error", path, msg))

    def warn(self, code, path, msg):
        self.findings.append(Finding(code, "warning", path, msg))

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == "warning"]

    def codes(self):
        return sorted({f.code for f in self.findings})


# --------------------------------------------------------------------------
# L1: structural
# --------------------------------------------------------------------------
def validate_structural(doc, report) -> bool:
    try:
        import jsonschema
    except ImportError:
        report.warn("AF001", "$", "jsonschema not installed; L1 structural validation skipped.")
        return True

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    ok = True
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        ok = False
        path = "$" + "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]"
                             for p in err.absolute_path)
        report.error("AF100", path, err.message)
    return ok


# --------------------------------------------------------------------------
# L2: semantic
# --------------------------------------------------------------------------
def validate_semantic(doc, report):
    matrix = json.loads(MATRIX_PATH.read_text())["edges"]

    nodes = doc.get("nodes", [])
    edges = doc.get("edges", [])

    # -- unique ids -------------------------------------------------------
    node_by_id: dict[str, dict] = {}
    for i, n in enumerate(nodes):
        nid = n.get("id")
        if nid in node_by_id:
            report.error("AF201", f"$['nodes'][{i}]", f"Duplicate node id {nid!r}.")
        else:
            node_by_id[nid] = n

    seen_edge_ids = set()
    for i, e in enumerate(edges):
        eid = e.get("id")
        if eid in seen_edge_ids:
            report.error("AF202", f"$['edges'][{i}]", f"Duplicate edge id {eid!r}.")
        seen_edge_ids.add(eid)

    registries: dict[str, dict[str, dict]] = {}
    for key in ("prompts", "models", "tools", "data_sources"):
        reg: dict[str, dict] = {}
        for i, item in enumerate(doc.get(key, [])):
            rid = item.get("id")
            if rid in reg:
                report.error("AF203", f"$['{key}'][{i}]", f"Duplicate {key} id {rid!r}.")
            else:
                reg[rid] = item
        registries[key] = reg

    # -- node refs resolve against the right registry ---------------------
    for i, n in enumerate(nodes):
        ref = n.get("ref")
        if ref is None:
            continue
        registry_key = REF_REGISTRY.get(n.get("type"))
        if registry_key is None:
            report.error("AF221", f"$['nodes'][{i}]['ref']",
                         f"Node type {n.get('type')!r} does not take a 'ref'.")
        elif ref not in registries[registry_key]:
            report.error("AF220", f"$['nodes'][{i}]['ref']",
                         f"ref {ref!r} not found in '{registry_key}' registry.")

    # -- depends_on resolves ----------------------------------------------
    for i, n in enumerate(nodes):
        for j, dep in enumerate(n.get("depends_on", [])):
            if dep not in node_by_id:
                report.error("AF260", f"$['nodes'][{i}]['depends_on'][{j}]",
                             f"depends_on references unknown node {dep!r}.")

    # -- failure_mode handler_node resolves -------------------------------
    for i, n in enumerate(nodes):
        for j, fm in enumerate((n.get("ai_context") or {}).get("failure_modes", [])):
            h = fm.get("handler_node")
            if h is not None and h not in node_by_id:
                report.error("AF271",
                             f"$['nodes'][{i}]['ai_context']['failure_modes'][{j}]['handler_node']",
                             f"handler_node references unknown node {h!r}.")

    # -- prompt variable source_node resolves -----------------------------
    for i, p in enumerate(doc.get("prompts", [])):
        for j, v in enumerate(p.get("variables", [])):
            sn = v.get("source_node")
            if sn is not None and sn not in node_by_id:
                report.error("AF270", f"$['prompts'][{i}]['variables'][{j}]['source_node']",
                             f"source_node references unknown node {sn!r}.")

    # -- edges: endpoints, type compatibility, ports ----------------------
    outgoing = defaultdict(list)
    adjacency = defaultdict(set)
    for i, e in enumerate(edges):
        path = f"$['edges'][{i}]"
        src_id, tgt_id, etype = e.get("source"), e.get("target"), e.get("type")
        src, tgt = node_by_id.get(src_id), node_by_id.get(tgt_id)

        if src is None:
            report.error("AF210", f"{path}['source']", f"Edge source {src_id!r} is not a declared node.")
        if tgt is None:
            report.error("AF211", f"{path}['target']", f"Edge target {tgt_id!r} is not a declared node.")
        if src is None or tgt is None:
            continue

        outgoing[src_id].append((i, e))
        adjacency[src_id].add(tgt_id)

        rule = matrix.get(etype)
        if rule:
            if src["type"] not in rule["source"]:
                report.error("AF230", f"{path}['source']",
                             f"'{etype}' cannot originate from a {src['type']!r} node "
                             f"(allowed: {', '.join(rule['source'])}).")
            if tgt["type"] not in rule["target"]:
                report.error("AF230", f"{path}['target']",
                             f"'{etype}' cannot target a {tgt['type']!r} node "
                             f"(allowed: {', '.join(rule['target'])}).")

        sp = e.get("source_port")
        if sp is not None and sp not in {p["name"] for p in src.get("outputs", [])}:
            report.error("AF240", f"{path}['source_port']",
                         f"Node {src_id!r} has no output port {sp!r}.")
        tp = e.get("target_port")
        if tp is not None and tp not in {p["name"] for p in tgt.get("inputs", [])}:
            report.error("AF241", f"{path}['target_port']",
                         f"Node {tgt_id!r} has no input port {tp!r}.")

    # -- condition node branch discipline ---------------------------------
    for nid, n in node_by_id.items():
        if n.get("type") != "condition":
            continue
        routes = [(i, e) for i, e in outgoing.get(nid, []) if e.get("type") == "routes_to"]
        if len(routes) < 2:
            report.warn("AF250", f"node:{nid}",
                        f"Condition node has {len(routes)} outgoing 'routes_to' edge(s); "
                        "a branch with fewer than two outcomes is not a branch.")
        defaults = [i for i, e in routes if e.get("default") is True]
        if len(defaults) > 1:
            report.error("AF251", f"node:{nid}",
                         f"Condition node has {len(defaults)} default branches; at most one is allowed.")

    # -- reachability ------------------------------------------------------
    inputs = [nid for nid, n in node_by_id.items() if n.get("type") == "input"]
    outputs = [nid for nid, n in node_by_id.items() if n.get("type") == "output"]

    if not inputs:
        report.warn("AF282", "$['nodes']", "Workflow declares no 'input' node; entry point is undefined.")
    if not outputs:
        report.warn("AF283", "$['nodes']", "Workflow declares no 'output' node; no terminal result is defined.")

    reached, stack = set(inputs), list(inputs)
    while stack:
        cur = stack.pop()
        for nxt in adjacency.get(cur, ()):
            if nxt not in reached:
                reached.add(nxt)
                stack.append(nxt)

    if inputs:
        for nid in node_by_id:
            if nid not in reached:
                report.warn("AF280", f"node:{nid}", "Node is not reachable from any 'input' node.")
        if outputs and not any(o in reached for o in outputs):
            report.error("AF281", "$['edges']",
                         "No 'output' node is reachable from any 'input' node; the workflow has no complete path.")

    # -- provenance discipline --------------------------------------------
    def check_prov(prov, path):
        if not prov:
            return
        if prov.get("method") == "ai_inference":
            conf = prov.get("confidence")
            if conf is not None and conf < LOW_CONFIDENCE:
                report.warn("AF290", path,
                            f"AI-inferred with confidence {conf}; below the {LOW_CONFIDENCE} review threshold.")
            if not prov.get("evidence") and not prov.get("reviewed_by"):
                report.warn("AF291", path,
                            "AI-inferred without supporting 'evidence' or a 'reviewed_by' sign-off.")

    for i, n in enumerate(nodes):
        check_prov(n.get("provenance"), f"$['nodes'][{i}]['provenance']")
    for i, e in enumerate(edges):
        check_prov(e.get("provenance"), f"$['edges'][{i}]['provenance']")
    for key in ("prompts", "models", "tools", "data_sources"):
        for i, item in enumerate(doc.get(key, [])):
            check_prov(item.get("provenance"), f"$['{key}'][{i}]['provenance']")


def validate(doc) -> Report:
    report = Report()
    if validate_structural(doc, report):
        validate_semantic(doc, report)
    else:
        report.warn("AF002", "$",
                    "L2 semantic validation skipped: document is not structurally valid.")
    return report


def main():
    ap = argparse.ArgumentParser(description="Validate a .aiflow document.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    ap.add_argument("--json", action="store_true", dest="as_json", help="Emit findings as JSON.")
    args = ap.parse_args()

    exit_code = 0
    for path in args.files:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"{path}: not valid JSON: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        report = validate(doc)
        failed = bool(report.errors) or (args.strict and bool(report.warnings))

        if args.as_json:
            print(json.dumps({"file": str(path), "ok": not failed,
                              "findings": [f.as_dict() for f in report.findings]}, indent=2))
        else:
            status = "FAIL" if failed else "OK"
            print(f"\n{path}  [{status}]  "
                  f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
            for f in report.findings:
                print(f"  {f}")

        if failed:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
