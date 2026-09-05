#!/usr/bin/env python3
"""Regenerate the diagrams embedded in README.md.

These assets are committed so the README renders on GitHub, which means they
can drift from the code that produces them. CI runs this script and fails if
the working tree changes, so a drifted diagram cannot be merged.

    python docs/build.py            # regenerate
    python docs/build.py --check    # fail if anything would change
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiflow import Document                      # noqa: E402
from aiflow.analyze import generate              # noqa: E402
from aiflow.svg import render_svg                # noqa: E402

DOCS = ROOT / "docs"
REFERENCE = ROOT / "examples" / "rag-support-agent.aiflow"
SAMPLE = ROOT / "examples" / "sample-project"


def assets() -> dict[Path, str]:
    """Every generated file, as path -> content."""
    out: dict[Path, str] = {}

    reference = Document.load(REFERENCE)
    extracted, _ = generate(SAMPLE)
    # the analyzer records the host repo's commit; that would churn every push
    extracted.project.commit = None
    extracted.project.repository = None

    for theme in ("light", "dark"):
        out[DOCS / f"workflow-{theme}.svg"] = render_svg(
            reference, theme=theme, orientation="horizontal")
        out[DOCS / f"generated-{theme}.svg"] = render_svg(
            extracted, theme=theme, orientation="horizontal")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if any asset is out of date.")
    args = ap.parse_args()

    stale: list[Path] = []
    for path, content in assets().items():
        current = path.read_text() if path.exists() else None
        if current == content:
            continue
        stale.append(path)
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    if args.check and stale:
        print("Diagrams are out of date. Run: python docs/build.py", file=sys.stderr)
        for p in stale:
            print(f"  {p.relative_to(ROOT)}", file=sys.stderr)
        return 1

    verb = "would update" if args.check else "wrote"
    print(f"{verb} {len(stale)} asset(s); {len(assets()) - len(stale)} already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
