"""Command-line interface for AIFLOW."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .diff import diff as diff_docs
from .graph import Graph
from .model import Document
from .validate import validate, validate_file

BOLD, DIM, RED, YELLOW, GREEN, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[0m")


def _color(stream=sys.stdout) -> bool:
    return stream.isatty()


def _paint(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if _color() else text


SKELETON = {
    "format": "aiflow",
    "version": "1.0",
    "project": {"name": None, "description": "", "languages": ["python"]},
    "nodes": [
        {"id": "in_request", "type": "input", "name": "Request",
         "outputs": [{"name": "text", "type": "string"}]},
        {"id": "out_response", "type": "output", "name": "Response",
         "inputs": [{"name": "answer", "type": "string"}]},
    ],
    "edges": [
        {"id": "e_request_to_response", "type": "passes",
         "source": "in_request", "target": "out_response",
         "source_port": "text", "target_port": "answer"},
    ],
    "metadata": {"generator": {"name": "aiflow", "version": __version__}},
    "provenance": {"method": "manual"},
}


# ---------------------------------------------------------------------------
def cmd_validate(args) -> int:
    exit_code = 0
    for path in args.files:
        try:
            report = validate_file(path)
        except FileNotFoundError:
            print(f"{path}: no such file", file=sys.stderr)
            exit_code = 1
            continue
        except json.JSONDecodeError as exc:
            print(f"{path}: not valid JSON: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        failed = bool(report.errors) or (args.strict and bool(report.warnings))
        if args.json:
            print(json.dumps({"file": str(path), "ok": not failed,
                              "findings": [f.as_dict() for f in report.findings]}, indent=2))
        else:
            status = _paint("FAIL", RED) if failed else _paint("OK", GREEN)
            print(f"\n{path}  [{status}]  "
                  f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
            for f in report.findings:
                print(f"  {f}")
        if failed:
            exit_code = 1
    return exit_code


def cmd_inspect(args) -> int:
    doc = Document.load(args.file)
    g = Graph(doc)

    selected = any([args.paths, args.rag, args.tools, args.unhandled,
                    args.inferred, args.node])

    if args.json:
        out: dict = {}
        if not selected or args.summary:
            out["summary"] = g.summary()
        if args.paths:
            out["paths"] = [{"nodes": list(p.nodes), "edges": list(p.edges)}
                            for p in g.paths_to_outputs()]
        if args.rag:
            out["rag"] = [n.id for n in g.rag_components()]
        if args.tools:
            out["agents_using_tools"] = {k: [t.id for t in v]
                                         for k, v in g.agents_using_tools().items()}
        if args.unhandled:
            out["unhandled_failures"] = [f.__dict__ for f in g.unhandled_failures()]
        if args.inferred:
            out["low_trust"] = [f.__dict__ for f in g.low_trust()]
        if args.node:
            n = doc.node(args.node)
            out["node"] = n.to_dict() if n else None
        print(json.dumps(out, indent=2))
        return 0 if (not args.node or doc.node(args.node)) else 1

    if not selected or args.summary:
        s = g.summary()
        title = (doc.project.name if doc.project else None) or args.file
        print(f"\n{_paint(str(title), BOLD)}  {_paint('v' + doc.version, DIM)}")
        if doc.project and doc.project.description:
            print(f"{doc.project.description}")
        print(f"\n  {s['nodes']} nodes, {s['edges']} edges, "
              f"{s['paths_to_output']} path(s) to output")
        print(f"  nodes  " + ", ".join(f"{k}={v}" for k, v in s["node_types"].items()))
        print(f"  edges  " + ", ".join(f"{k}={v}" for k, v in s["edge_types"].items()))
        print(f"  registries  " + ", ".join(f"{k}={v}" for k, v in s["registries"].items()))
        if s["inferred_elements"]:
            print(f"  {_paint(str(s['inferred_elements']) + ' AI-inferred element(s)', YELLOW)}")

    if args.paths:
        print(f"\n{_paint('Paths to output', BOLD)}")
        for p in g.paths_to_outputs():
            print(f"  {p.render(doc)}")

    if args.rag:
        print(f"\n{_paint('Retrieval surface', BOLD)}")
        for n in g.rag_components():
            entry = doc.resolve(n)
            extra = f"  {DIM}-> {entry.id}{RESET}" if entry and _color() else ""
            print(f"  {n.id} ({n.type}){extra}")

    if args.tools:
        print(f"\n{_paint('Agents using tools', BOLD)}")
        mapping = g.agents_using_tools()
        if not mapping:
            print("  none")
        for agent, tools in mapping.items():
            print(f"  {agent} -> {', '.join(t.id for t in tools)}")

    if args.unhandled:
        print(f"\n{_paint('Unhandled failure modes', BOLD)}")
        findings = g.unhandled_failures()
        if not findings:
            print("  none declared")
        for f in findings:
            loc = f"  {DIM}({f.source}){RESET}" if f.source and _color() else ""
            print(f"  {_paint(f.subject, YELLOW)}: {f.detail}{loc}")

    if args.inferred:
        print(f"\n{_paint('Low-trust claims', BOLD)}")
        findings = g.low_trust(args.threshold)
        if not findings:
            print("  none")
        for f in findings:
            print(f"  {_paint(f.subject, YELLOW)}: {f.detail}")

    if args.node:
        n = doc.node(args.node)
        if n is None:
            print(f"\nno node with id {args.node!r}", file=sys.stderr)
            return 1
        print(f"\n{_paint(n.id, BOLD)}  ({n.type})")
        if n.name:
            print(f"  name         {n.name}")
        if n.description:
            print(f"  description  {n.description}")
        entry = doc.resolve(n)
        if entry:
            print(f"  ref          {n.ref} -> {type(entry).__name__}")
        for src in n.source or ():
            print(f"  source       {src}" + (f"  {src.symbol}" if src.symbol else ""))
        if n.provenance:
            conf = f" confidence={n.provenance.confidence}" if n.provenance.confidence is not None else ""
            print(f"  provenance   {n.provenance.method}{conf}")
        if n.ai_context:
            if n.ai_context.summary:
                print(f"  summary      {n.ai_context.summary}")
            for fm in n.ai_context.failure_modes or ():
                mark = "handled" if fm.handled else _paint("UNHANDLED", YELLOW)
                print(f"  failure      [{mark}] {fm.description}")
        incoming = [f"{e.source} --{e.type}-->" for e in g.in_edges(n.id)]
        outgoing = [f"--{e.type}--> {e.target}" for e in g.out_edges(n.id)]
        for line in incoming:
            print(f"  in           {line}")
        for line in outgoing:
            print(f"  out          {line}")

    print()
    return 0


def cmd_init(args) -> int:
    target = Path(args.output)
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    skeleton = json.loads(json.dumps(SKELETON))
    skeleton["project"]["name"] = args.name or Path.cwd().name
    report = validate(skeleton)
    if report.errors:
        print("internal error: skeleton does not validate", file=sys.stderr)
        for f in report.errors:
            print(f"  {f}", file=sys.stderr)
        return 1
    target.write_text(json.dumps(skeleton, indent=2) + "\n")
    print(f"wrote {target}")
    return 0


def cmd_diff(args) -> int:
    result = diff_docs(Document.load(args.old), Document.load(args.new))
    if args.json:
        print(json.dumps({"changed": bool(result),
                          "summary": result.summary(),
                          "changes": [c.__dict__ for c in result.changes]}, indent=2))
    elif not result:
        print("no semantic changes")
    else:
        for c in result.changes:
            print(c)
        print(f"\n{len(result.changes)} change(s)")
    return 1 if (result and args.exit_code) else 0


def cmd_unimplemented(args) -> int:
    print(f"'aiflow {args.command}' is not implemented yet.\n"
          f"It lands in a later phase of the roadmap; see README.md.", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aiflow",
                                description="Work with .aiflow semantic workflow documents.")
    p.add_argument("--version", action="version", version=f"aiflow {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="Validate .aiflow documents (structural + semantic).")
    v.add_argument("files", nargs="+", type=Path)
    v.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_validate)

    i = sub.add_parser("inspect", help="Explore a workflow and ask behavioral questions.")
    i.add_argument("file", type=Path)
    i.add_argument("--summary", action="store_true", help="Force the summary alongside other views.")
    i.add_argument("--paths", action="store_true", help="All paths from input to output.")
    i.add_argument("--rag", action="store_true", help="Retrieval surface.")
    i.add_argument("--tools", action="store_true", help="Which agents can invoke which tools.")
    i.add_argument("--unhandled", action="store_true", help="Declared failure modes with no handler.")
    i.add_argument("--inferred", action="store_true", help="AI-inferred claims not yet reviewed.")
    i.add_argument("--threshold", type=float, default=0.60, help="Confidence floor for --inferred.")
    i.add_argument("--node", metavar="ID", help="Detail for a single node.")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_inspect)

    n = sub.add_parser("init", help="Write a minimal valid .aiflow skeleton.")
    n.add_argument("-o", "--output", default="project.aiflow", type=Path)
    n.add_argument("--name", help="Project name (defaults to the directory name).")
    n.add_argument("--force", action="store_true")
    n.set_defaults(func=cmd_init)

    d = sub.add_parser("diff", help="Semantic diff between two documents.")
    d.add_argument("old", type=Path)
    d.add_argument("new", type=Path)
    d.add_argument("--json", action="store_true")
    d.add_argument("--exit-code", action="store_true",
                   help="Exit 1 when the documents differ.")
    d.set_defaults(func=cmd_diff)

    for name, helptext in (("generate", "Extract a .aiflow from a project (phase 5-7)."),
                           ("render", "Render a .aiflow to an interactive graph (phase 4).")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("args", nargs="*")
        s.set_defaults(func=cmd_unimplemented)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
