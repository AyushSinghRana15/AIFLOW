"""Command-line interface for AIFLOW."""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from . import __version__
from .diff import diff as diff_docs
from .graph import Graph
from .model import Document
from .render import render_to_file
from .svg import render_svg_to_file
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
    args.summary = False

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
            tag = f" {_paint('[inferred]', DIM)}" if f.inferred else ""
            print(f"  {_paint(f.subject, YELLOW)}{tag}: {f.detail}{loc}")

    if args.inferred:
        print(f"\n{_paint('Low-trust claims', BOLD)}")
        findings = g.low_trust()
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


def cmd_generate(args) -> int:
    from .analyze import generate

    root = Path(args.path)
    if not root.exists():
        print(f"{root}: no such path", file=sys.stderr)
        return 1

    doc, reports = generate(root, name=args.name)
    report = validate(doc.to_dict())

    out = args.output or Path("project.aiflow")
    if out.exists() and not args.force:
        print(f"{out} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    doc.save(out)

    counts: dict[str, int] = {}
    for n in doc.nodes:
        counts[n.type] = counts.get(n.type, 0) + 1
    print(f"wrote {out}")
    print(f"  analyzed {len(reports)} file(s) -> {len(doc.nodes)} nodes, "
          f"{len(doc.edges)} edges")
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if report.errors:
        print(f"\n  {len(report.errors)} validation error(s):", file=sys.stderr)
        for f in report.errors[:5]:
            print(f"    {f}", file=sys.stderr)
        return 1
    for f in report.warnings:
        print(f"  {_paint('warning', YELLOW)} {f.code} {f.message}")

    # Say plainly what static analysis could not see, rather than letting an
    # incomplete graph read as a complete one.
    if not any(n.type == "input" for n in doc.nodes):
        print(f"  {_paint('note', DIM)} no entrypoint found; the graph has no "
              f"declared start. Add one by hand, or point --path at the module "
              f"that receives requests.")
    if not any(n.type == "condition" for n in doc.nodes):
        print(f"  {_paint('note', DIM)} branching is not extracted by static "
              f"analysis; add condition nodes by hand where the workflow routes.")
    return 0


def _render(doc, out: Path, orientation: str, theme: str) -> Path:
    """Format follows the output extension: .svg exports a static diagram,
    anything else writes the interactive page."""
    if out.suffix.lower() == ".svg":
        return render_svg_to_file(doc, out, theme=theme, orientation=orientation)
    return render_to_file(doc, out, orientation=orientation)


def cmd_enrich(args) -> int:
    from .semantic import (BudgetExceeded, Cache, ClientError, Ledger,
                           MissingKey, OpenRouterClient, enrich)

    ledger = Ledger.open(args.limit_per_day)
    if args.status:
        print(f"API calls today: {ledger.used_today()} of {ledger.limit} "
              f"({ledger.remaining()} left)")
        print(f"state: {ledger.path}")
        return 0

    doc = Document.load(args.file)
    cache = Cache.open(enabled=not args.no_cache)

    if args.dry_run:
        result = enrich(doc, cache=cache, overwrite=args.overwrite,
                        dry_run=True, limit=args.batches)
        for note in result.notes:
            print(f"  {note}")
        print(f"  budget: {ledger.remaining()} of {ledger.limit} call(s) left today")
        return 0

    try:
        client = OpenRouterClient.from_env(args.model)
    except MissingKey as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        result = enrich(doc, client=client, ledger=ledger, cache=cache,
                        overwrite=args.overwrite, limit=args.batches)
    except BudgetExceeded as exc:
        print(f"{_paint('budget', YELLOW)} {exc}", file=sys.stderr)
        return 1
    except ClientError as exc:
        print(f"{_paint('error', RED)} {exc}", file=sys.stderr)
        return 1

    report = validate(doc.to_dict())
    if report.errors:
        print(f"{_paint('error', RED)} enrichment produced an invalid document; "
              f"not writing.", file=sys.stderr)
        for f in report.errors[:5]:
            print(f"  {f}", file=sys.stderr)
        return 1

    out = args.output or args.file
    doc.save(out)
    print(f"wrote {out}")
    print(f"  enriched {len(result.enriched)} node(s) in {result.batches} batch(es)")
    print(f"  {result.calls_made} API call(s), {result.cache_hits} from cache; "
          f"{ledger.remaining()} of {ledger.limit} left today")
    if result.skipped:
        print(f"  {_paint('skipped', DIM)} {len(result.skipped)} node(s) the model "
              f"could not describe from the graph alone")
    for note in result.notes:
        print(f"  {note}")
    print(f"  {_paint('note', DIM)} every added description is ai_inference — "
          f"review with: aiflow inspect {out} --inferred")
    return 0


def cmd_render(args) -> int:
    doc = Document.load(args.file)
    report = validate(doc.to_dict())
    if report.errors and not args.force:
        print(f"{args.file} has {len(report.errors)} validation error(s); "
              f"rendering an invalid document would misrepresent it.\n"
              f"Fix them, or pass --force to render anyway.", file=sys.stderr)
        for f in report.errors[:5]:
            print(f"  {f}", file=sys.stderr)
        return 1

    out = args.output or Path(args.file).with_suffix(".html")
    _render(doc, out, args.orientation, args.theme)
    print(f"wrote {out}")
    if args.open:
        import webbrowser
        webbrowser.open(Path(out).resolve().as_uri())
    return 0


def cmd_view(args) -> int:
    """One command from a source tree or a document to a diagram on screen."""
    target = Path(args.target)
    if not target.exists():
        print(f"{target}: no such path", file=sys.stderr)
        return 1

    if target.is_dir():
        from .analyze import generate
        doc, reports = generate(target, name=args.name)
        print(f"analyzed {len(reports)} file(s) -> {len(doc.nodes)} nodes, "
              f"{len(doc.edges)} edges")
        if args.save:
            doc.save(args.save)
            print(f"wrote {args.save}")
    else:
        doc = Document.load(target)

    report = validate(doc.to_dict())
    for f in report.errors:
        print(f"  {_paint('error', RED)} {f.code} {f.message}", file=sys.stderr)
    if report.errors:
        return 1

    out = args.output or Path(tempfile.gettempdir()) / f"{_slug(target)}.html"
    _render(doc, Path(out), "horizontal", args.theme)
    print(f"wrote {out}")
    if not args.no_open:
        import webbrowser
        webbrowser.open(Path(out).resolve().as_uri())
    return 0


def _slug(path: Path) -> str:
    stem = path.resolve().name or "workflow"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", stem) or "workflow"


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
    i.add_argument("--paths", action="store_true", help="All paths from input to output.")
    i.add_argument("--rag", action="store_true", help="Retrieval surface.")
    i.add_argument("--tools", action="store_true", help="Which agents can invoke which tools.")
    i.add_argument("--unhandled", action="store_true", help="Declared failure modes with no handler.")
    i.add_argument("--inferred", action="store_true", help="AI-inferred claims not yet reviewed.")
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

    r = sub.add_parser("render", help="Render a workflow to a self-contained HTML page.")
    r.add_argument("file", type=Path)
    r.add_argument("-o", "--output", type=Path,
                   help="Output path (defaults to the input with a .html suffix).")
    r.add_argument("--orientation", choices=["horizontal", "vertical"], default="horizontal")
    r.add_argument("--theme", choices=["light", "dark"], default="light",
                   help="Colour scheme for .svg output; the HTML page follows the viewer.")
    r.add_argument("--open", action="store_true", help="Open the result in a browser.")
    r.add_argument("--force", action="store_true", help="Render even if validation fails.")
    r.set_defaults(func=cmd_render)

    w = sub.add_parser("view", help="Analyze or open a workflow and show it in a browser.")
    w.add_argument("target", nargs="?", default=".", type=Path,
                   help="A project directory to analyze, or an existing .aiflow file.")
    w.add_argument("-o", "--output", type=Path, help="Where to write the page.")
    w.add_argument("--save", type=Path, help="Also save the extracted .aiflow document.")
    w.add_argument("--name", help="Project name, when analyzing a directory.")
    w.add_argument("--theme", choices=["light", "dark"], default="light")
    w.add_argument("--no-open", action="store_true", help="Write the page but do not open it.")
    w.set_defaults(func=cmd_view)

    en = sub.add_parser("enrich", help="Add inferred semantics to a document using a model.")
    en.add_argument("file", nargs="?", type=Path)
    en.add_argument("-o", "--output", type=Path, help="Write here instead of in place.")
    en.add_argument("--dry-run", action="store_true",
                    help="Report what it would cost without calling the API.")
    en.add_argument("--overwrite", action="store_true",
                    help="Replace existing ai_context instead of only filling gaps.")
    en.add_argument("--batches", type=int, metavar="N",
                    help="Process at most N batches this run.")
    en.add_argument("--model", help=f"OpenRouter model id (or set {'AIFLOW_MODEL'}).")
    en.add_argument("--limit-per-day", type=int, metavar="N",
                    help="Override the daily call cap for this run.")
    en.add_argument("--no-cache", action="store_true", help="Ignore cached responses.")
    en.add_argument("--status", action="store_true",
                    help="Show today's API usage and exit.")
    en.set_defaults(func=cmd_enrich)

    gen = sub.add_parser("generate", help="Extract a .aiflow from a Python project.")
    gen.add_argument("path", nargs="?", default=".", type=Path)
    gen.add_argument("-o", "--output", type=Path,
                     help="Output path (defaults to project.aiflow).")
    gen.add_argument("--name", help="Project name (defaults to the directory name).")
    gen.add_argument("--force", action="store_true")
    gen.set_defaults(func=cmd_generate)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
