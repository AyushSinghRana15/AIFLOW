#!/usr/bin/env python3
"""AIFLOW v1 conformance tests.

Three suites:
  1. golden      -- the reference example validates clean
  2. matrix      -- exhaustive sweep of every (edge_type x source_type x target_type)
                    combination, asserting the validator agrees with
                    spec/edge-compatibility.json in both directions
  3. mutations   -- a table of targeted defects, each asserting a specific
                    diagnostic code fires
"""
from __future__ import annotations

import copy
import json
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiflow import spec  # noqa: E402
from aiflow.validate import validate  # noqa: E402

EDGE_RULES = spec.edge_rules()
NODE_TYPES = spec.node_types()
GOLDEN = json.loads((ROOT / "examples" / "rag-support-agent.aiflow").read_text())

REF_FOR = {"llm": "m_x", "tool": "t_x", "prompt": "p_x", "vector_store": "ds_x"}

RESET = "\033[0m"; RED = "\033[31m"; GREEN = "\033[32m"; DIM = "\033[2m"


class Results:
    def __init__(self):
        self.passed = 0
        self.failures: list[str] = []

    def check(self, ok: bool, label: str, detail: str = ""):
        if ok:
            self.passed += 1
        else:
            self.failures.append(f"{label}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# suite 1: golden file
# ---------------------------------------------------------------------------
def suite_golden(r: Results):
    report = validate(copy.deepcopy(GOLDEN))
    r.check(not report.errors, "golden: reference example has no errors",
            "; ".join(f"{f.code} {f.path}" for f in report.errors))
    r.check(not report.warnings, "golden: reference example has no warnings",
            "; ".join(f"{f.code} {f.path}" for f in report.warnings))

    covered_edges = {e["type"] for e in GOLDEN["edges"]}
    r.check(covered_edges == set(EDGE_RULES),
            "golden: exercises every edge type",
            f"missing {sorted(set(EDGE_RULES) - covered_edges)}")

    covered_nodes = {n["type"] for n in GOLDEN["nodes"]}
    r.check(covered_nodes == set(NODE_TYPES),
            "golden: exercises every node type",
            f"missing {sorted(set(NODE_TYPES) - covered_nodes)}")


# ---------------------------------------------------------------------------
# suite 2: exhaustive compatibility matrix sweep
# ---------------------------------------------------------------------------
def build_pair(edge_type: str, src_type: str, tgt_type: str) -> dict:
    """Minimal two-node document isolating one edge-type/node-type combination."""
    def node(nid, ntype):
        n = {"id": nid, "type": ntype,
             "inputs": [{"name": "i"}], "outputs": [{"name": "o"}]}
        if ntype in REF_FOR:
            n["ref"] = REF_FOR[ntype]
        elif ntype == "retriever":
            n["ref"] = "ds_x"
        return n

    edge = {"id": "e1", "type": edge_type, "source": "n_src", "target": "n_tgt"}
    if edge_type == "routes_to":
        edge["when"] = "always"

    return {
        "format": "aiflow", "version": "1.0",
        "nodes": [node("n_src", src_type), node("n_tgt", tgt_type)],
        "edges": [edge],
        "prompts": [{"id": "p_x"}],
        "models": [{"id": "m_x", "provider": "anthropic", "model": "claude-sonnet-4-5"}],
        "tools": [{"id": "t_x", "name": "x"}],
        "data_sources": [{"id": "ds_x", "kind": "vector_store"}],
    }


def suite_matrix(r: Results):
    combos = 0
    for edge_type, rule in EDGE_RULES.items():
        for src_type, tgt_type in product(NODE_TYPES, NODE_TYPES):
            combos += 1
            expected_ok = src_type in rule["source"] and tgt_type in rule["target"]
            report = validate(build_pair(edge_type, src_type, tgt_type))
            got_af230 = "AF230" in report.codes()
            # AF230 must fire exactly when the matrix forbids the combination
            r.check(got_af230 != expected_ok,
                    f"matrix: {edge_type} {src_type}->{tgt_type} "
                    f"({'allowed' if expected_ok else 'forbidden'})",
                    f"AF230 {'fired' if got_af230 else 'did not fire'}; "
                    f"codes={report.codes()}")
    print(f"{DIM}  swept {combos} edge/node-type combinations{RESET}")


# ---------------------------------------------------------------------------
# suite 3: mutation table
# ---------------------------------------------------------------------------
def mutate(fn):
    doc = copy.deepcopy(GOLDEN)
    fn(doc)
    return doc


def node_idx(doc, nid):
    return next(i for i, n in enumerate(doc["nodes"]) if n["id"] == nid)


def edge_idx(doc, eid):
    return next(i for i, e in enumerate(doc["edges"]) if e["id"] == eid)


def _dup_node(d):
    d["nodes"].append(copy.deepcopy(d["nodes"][node_idx(d, "retr_kb")]))

def _dup_edge(d):
    d["edges"].append(copy.deepcopy(d["edges"][edge_idx(d, "e_route_kb")]))

def _dup_registry(d):
    d["models"].append({"id": "m_haiku", "provider": "openai", "model": "gpt-4o"})

def _bad_source(d):
    d["edges"][edge_idx(d, "e_docs_to_answerer")]["source"] = "nope"

def _bad_target(d):
    d["edges"][edge_idx(d, "e_docs_to_answerer")]["target"] = "nope"

def _bad_ref(d):
    d["nodes"][node_idx(d, "llm_answer")]["ref"] = "m_missing"

def _ref_on_agent(d):
    d["nodes"][node_idx(d, "agent_supervisor")]["ref"] = "m_haiku"

def _bad_edge_type(d):
    # a prompt cannot call an llm
    d["edges"][edge_idx(d, "e_supervisor_calls_router_llm")]["source"] = "prompt_router"

def _bad_source_port(d):
    d["edges"][edge_idx(d, "e_docs_to_answerer")]["source_port"] = "ghost"

def _bad_target_port(d):
    d["edges"][edge_idx(d, "e_docs_to_answerer")]["target_port"] = "ghost"

def _two_defaults(d):
    d["edges"][edge_idx(d, "e_route_kb")].pop("when")
    d["edges"][edge_idx(d, "e_route_kb")]["default"] = True

def _routes_without_predicate(d):
    d["edges"][edge_idx(d, "e_route_kb")].pop("when")

def _bad_depends_on(d):
    d["nodes"][node_idx(d, "agent_answerer")]["depends_on"] = ["ghost_node"]

def _bad_prompt_var(d):
    d["prompts"][0]["variables"][0]["source_node"] = "ghost_node"

def _bad_handler(d):
    d["nodes"][node_idx(d, "agent_supervisor")]["ai_context"]["failure_modes"][0]["handler_node"] = "ghost"

def _orphan_node(d):
    d["nodes"].append({"id": "orphan_agent", "type": "agent"})

def _sever_path(d):
    d["edges"] = [e for e in d["edges"] if e["id"] != "e_query_to_supervisor"]

def _low_confidence(d):
    d["edges"][edge_idx(d, "e_ticket_to_answerer")]["provenance"]["confidence"] = 0.31

def _unevidenced_inference(d):
    d["edges"][edge_idx(d, "e_ticket_to_answerer")]["provenance"].pop("evidence")

def _inference_without_confidence(d):
    d["edges"][edge_idx(d, "e_ticket_to_answerer")]["provenance"].pop("confidence")

def _missing_required(d):
    d["nodes"][node_idx(d, "retr_kb")].pop("type")

def _bad_node_type(d):
    d["nodes"][node_idx(d, "retr_kb")]["type"] = "wizard"

def _missing_ref(d):
    d["nodes"][node_idx(d, "llm_answer")].pop("ref")

def _typo_property(d):
    d["nodes"][node_idx(d, "retr_kb")]["descriptoin"] = "typo"

def _bad_version(d):
    d["version"] = "2.0"

def _absolute_source_path(d):
    d["nodes"][node_idx(d, "retr_kb")]["source"][0]["file"] = "rag/retriever.py"  # control: stays valid

def _extensions_escape_hatch(d):
    d["nodes"][node_idx(d, "retr_kb")]["extensions"] = {"langgraph": {"checkpoint": "redis", "anything": [1, 2]}}


MUTATIONS = [
    # (label, mutation, expected code, expected severity)
    ("duplicate node id",                    _dup_node,                    "AF201", "error"),
    ("duplicate edge id",                    _dup_edge,                    "AF202", "error"),
    ("duplicate registry id",                _dup_registry,                "AF203", "error"),
    ("edge source not a node",               _bad_source,                  "AF210", "error"),
    ("edge target not a node",               _bad_target,                  "AF211", "error"),
    ("node ref unresolved",                  _bad_ref,                     "AF220", "error"),
    ("ref on a node type that forbids it",   _ref_on_agent,                "AF100", "error"),
    ("edge type/node type mismatch",         _bad_edge_type,               "AF230", "error"),
    ("source_port does not exist",           _bad_source_port,             "AF240", "error"),
    ("target_port does not exist",           _bad_target_port,             "AF241", "error"),
    ("two default branches",                 _two_defaults,                "AF251", "error"),
    ("routes_to without when or default",    _routes_without_predicate,    "AF100", "error"),
    ("depends_on unresolved",                _bad_depends_on,              "AF260", "error"),
    ("prompt variable source_node unresolved", _bad_prompt_var,            "AF270", "error"),
    ("failure_mode handler_node unresolved", _bad_handler,                 "AF271", "error"),
    ("unreachable node",                     _orphan_node,                 "AF280", "warning"),
    ("no input->output path",                _sever_path,                  "AF281", "error"),
    ("low-confidence inference",             _low_confidence,              "AF290", "warning"),
    ("inference without evidence",           _unevidenced_inference,       "AF291", "warning"),
    ("ai_inference without confidence",      _inference_without_confidence, "AF100", "error"),
    ("missing required property",            _missing_required,            "AF100", "error"),
    ("node type outside enum",               _bad_node_type,               "AF100", "error"),
    ("required ref omitted",                 _missing_ref,                 "AF100", "error"),
    ("misspelled property rejected",         _typo_property,               "AF100", "error"),
    ("unsupported major version",            _bad_version,                 "AF100", "error"),
]

CLEAN_MUTATIONS = [
    ("relative source path stays valid",     _absolute_source_path),
    ("extensions accept arbitrary data",     _extensions_escape_hatch),
]


def suite_mutations(r: Results):
    for label, fn, code, severity in MUTATIONS:
        report = validate(mutate(fn))
        hits = [f for f in report.findings if f.code == code]
        r.check(bool(hits), f"mutation: {label} -> {code}",
                f"got codes={report.codes()}")
        if hits:
            r.check(hits[0].severity == severity,
                    f"mutation: {label} severity is {severity}",
                    f"got {hits[0].severity}")

    for label, fn in CLEAN_MUTATIONS:
        report = validate(mutate(fn))
        r.check(not report.errors and not report.warnings,
                f"clean: {label}",
                "; ".join(f"{f.code} {f.path}" for f in report.findings))


# ---------------------------------------------------------------------------
def main():
    r = Results()
    for name, suite in (("golden", suite_golden),
                        ("matrix", suite_matrix),
                        ("mutations", suite_mutations)):
        print(f"\n{name}")
        suite(r)

    total = r.passed + len(r.failures)
    print()
    if r.failures:
        for f in r.failures:
            print(f"  {RED}FAIL{RESET} {f}")
        print(f"\n{RED}{len(r.failures)} failed{RESET}, {r.passed} passed, {total} total")
        return 1
    print(f"{GREEN}all {total} assertions passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
