#!/usr/bin/env python3
"""Documentation integrity tests.

The README is the project's front door and it embeds three things that can
break silently: relative links, generated diagrams, and Mermaid blocks that
GitHub parses at render time. A Mermaid block using a reserved word as an
identifier renders as an error box to every visitor while looking fine in the
source, so it is linted here rather than discovered in the wild.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

RESET, RED, GREEN = "\033[0m", "\033[31m", "\033[32m"

# Identifiers Mermaid's flowchart grammar will not accept as node or subgraph names.
MERMAID_RESERVED = {
    "graph", "subgraph", "end", "flowchart", "class", "classdef", "click",
    "style", "linkstyle", "direction", "default", "call", "href", "o", "x",
}

EDGE = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*(?:-{2,3}>|-\.->|={2,3}>|-{3}|-\.-)")
SUBGRAPH = re.compile(r"^\s*subgraph\s+([A-Za-z_][\w-]*)")


class Results:
    def __init__(self):
        self.passed = 0
        self.failures: list[str] = []

    def check(self, ok, label, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failures.append(f"{label}{(' — ' + str(detail)) if detail else ''}")


def slug(heading: str) -> str:
    h = heading.lower().replace("`", "")
    h = re.sub(r"[^\w\s-]", "", h)
    return re.sub(r"\s+", "-", h.strip())


def suite_links(r: Results, text: str):
    broken = []
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if not (ROOT / target.split("#")[0]).exists():
            broken.append(target)
    r.check(not broken, "docs: every relative link resolves", broken)

    images = re.findall(r'(?:src|srcset)="([^"]+)"', text)
    missing = [i for i in images
               if not i.startswith("http") and not (ROOT / i).exists()]
    r.check(not missing, "docs: every referenced image exists", missing)

    headings = {slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}
    anchors = re.findall(r"\]\(#([\w-]+)\)", text)
    dangling = sorted({a for a in anchors if a not in headings})
    r.check(not dangling, "docs: every internal anchor resolves", dangling)
    r.check(len(anchors) > 5, "docs: the README has a table of contents")


def suite_mermaid(r: Results, text: str):
    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
    r.check(bool(blocks), "docs: the README contains Mermaid diagrams")

    for i, block in enumerate(blocks):
        offenders = []
        for line in block.splitlines():
            for match in (EDGE.match(line), SUBGRAPH.match(line)):
                if match and match.group(1).lower() in MERMAID_RESERVED:
                    offenders.append(match.group(1))
        r.check(not offenders,
                f"mermaid: block {i} uses no reserved word as an identifier",
                sorted(set(offenders)))

        opens = len(re.findall(r"^\s*subgraph\b", block, re.M))
        ends = len(re.findall(r"^\s*end\s*$", block, re.M))
        r.check(opens == ends,
                f"mermaid: block {i} closes every subgraph",
                f"{opens} subgraph vs {ends} end")

        first = next((l.strip() for l in block.splitlines() if l.strip()), "")
        r.check(first.split()[0] in {"flowchart", "graph", "sequenceDiagram",
                                     "stateDiagram-v2", "classDiagram", "erDiagram"},
                f"mermaid: block {i} declares a diagram type", first[:40])


def suite_assets(r: Results):
    build = ROOT / "docs" / "build.py"
    r.check(build.is_file(), "docs: the asset builder is present")
    for name in ("workflow-light.svg", "workflow-dark.svg",
                 "generated-light.svg", "generated-dark.svg"):
        path = ROOT / "docs" / name
        r.check(path.is_file() and path.stat().st_size > 1000,
                f"docs: {name} is committed and non-trivial")
        if path.is_file():
            r.check(path.read_text().startswith("<svg"),
                    f"docs: {name} is an SVG")


def suite_plugin(r: Results):
    plugin = ROOT / "claude-plugin" / ".claude-plugin" / "plugin.json"
    market = ROOT / ".claude-plugin" / "marketplace.json"
    r.check(plugin.is_file(), "plugin: manifest is present")
    r.check(market.is_file(), "plugin: marketplace is present")
    if not (plugin.is_file() and market.is_file()):
        return

    p, m = json.loads(plugin.read_text()), json.loads(market.read_text())
    kebab = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
    r.check(bool(kebab.fullmatch(p["name"])), "plugin: name is kebab-case", p["name"])
    r.check(bool(kebab.fullmatch(m["name"])), "plugin: marketplace name is kebab-case")
    r.check(p["name"] in {e["name"] for e in m["plugins"]},
            "plugin: the manifest is listed in the marketplace")

    for entry in m["plugins"]:
        src = entry["source"]
        if isinstance(src, str) and src.startswith("./"):
            r.check((ROOT / src).is_dir(), f"plugin: source {src} exists")

    skills = sorted((ROOT / "claude-plugin" / "skills").glob("*/SKILL.md"))
    r.check(len(skills) >= 1, "plugin: at least one skill is present")
    for skill in skills:
        body = skill.read_text()
        r.check(body.startswith("---"), f"plugin: {skill.parent.name} has frontmatter")
        fm = body.split("---")[1]
        name = re.search(r"^name:\s*(.+)$", fm, re.M)
        desc = re.search(r"^description:\s*(.+)$", fm, re.M)
        r.check(name and name.group(1).strip() == skill.parent.name,
                f"plugin: {skill.parent.name} name matches its directory")
        r.check(desc and len(desc.group(1)) > 40,
                f"plugin: {skill.parent.name} description says when to use it")
        # a skill that shells out must not assume the CLI is installed
        r.check("AIFLOW_NOT_INSTALLED" in body,
                f"plugin: {skill.parent.name} handles a missing CLI")


def suite_project_files(r: Results):
    """The files that make this an open specification rather than one program."""
    for name in ("CONTRIBUTING.md", "CHANGELOG.md", "LICENSE",
                 "docs/CONFORMANCE.md", "docs/ADAPTERS.md",
                 ".pre-commit-hooks.yaml", "action.yml"):
        path = ROOT / name
        r.check(path.is_file() and path.stat().st_size > 200,
                f"project: {name} is present and substantive")

    for name in ("bug_report.md", "adapter_request.md", "spec_change.md"):
        path = ROOT / ".github" / "ISSUE_TEMPLATE" / name
        r.check(path.is_file() and path.read_text().startswith("---"),
                f"project: the {name} template has frontmatter")

    r.check((ROOT / ".github" / "pull_request_template.md").is_file(),
            "project: a pull request template is present")

    for name in ("ci.yml", "publish.yml"):
        r.check((ROOT / ".github" / "workflows" / name).is_file(),
                f"project: the {name} workflow is present")

    # every AF code the validator can emit must be documented for implementers
    validator = (ROOT / "aiflow" / "validate.py").read_text()
    emitted = set(re.findall(r'"(AF\d{3})"', validator))
    documented = set(re.findall(r"AF\d{3}", (ROOT / "spec" / "SPEC.md").read_text()))
    documented |= set(re.findall(r"AF\d{3}", (ROOT / "docs" / "CONFORMANCE.md").read_text()))
    undocumented = sorted(emitted - documented - {"AF001", "AF002"})
    r.check(not undocumented,
            "project: every diagnostic code the validator emits is documented",
            undocumented)

    conformance = (ROOT / "docs" / "CONFORMANCE.md").read_text()
    for required in ("edge-compatibility.json", "lossless", "provenance"):
        r.check(required in conformance,
                f"conformance: the document covers {required}")

    spec = (ROOT / "spec" / "SPEC.md").read_text()
    r.check("## 10. Governance" in spec, "spec: governance is documented")
    r.check("| **1.1** |" in spec, "spec: the version table records 1.1")


def main() -> int:
    r = Results()
    text = README.read_text()
    for name, suite in (("links", lambda x: suite_links(x, text)),
                        ("mermaid", lambda x: suite_mermaid(x, text)),
                        ("assets", suite_assets),
                        ("plugin", suite_plugin),
                        ("project", suite_project_files)):
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
