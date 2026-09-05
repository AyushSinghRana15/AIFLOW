"""Static analysis of a Python project into AIFLOW findings.

What this module will and will not claim
----------------------------------------
It reports only what the syntax tree shows: a call whose shape matches a known
LLM invocation, a class whose method contains one, a string constant that
reaches a prompt argument. Every finding carries `static_analysis` provenance
and the confidence of the rule that matched.

It does **not** infer intent, summaries, or failure modes. Those are the job of
the semantic analyzer, and inventing them here would put unfalsifiable prose
behind a `static_analysis` label -- exactly the confusion the provenance model
exists to prevent. Generated documents therefore carry no `ai_context`.

Dynamic construction is the known blind spot: a graph assembled at runtime from
configuration is invisible to an AST walk, and the analyzer says so rather than
guessing.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import signatures as sig

__all__ = ["Finding", "FileReport", "analyze_path", "analyze_source"]

SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "node_modules",
             ".tox", ".mypy_cache", ".pytest_cache", "build", "dist",
             "site-packages", ".eggs"}
PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}|\{\{\s*[a-zA-Z_]")


@dataclass
class Finding:
    kind: str                      # llm|tool|prompt|agent|retriever|vector_store|model|entrypoint
    key: str                       # stable identity, used to dedupe and to mint ids
    name: str
    file: str
    line: int
    end_line: int | None = None
    symbol: str | None = None
    confidence: float = 0.8
    scope: str | None = None       # enclosing class or function qualname
    data: dict = field(default_factory=dict)


@dataclass
class FileReport:
    path: str
    findings: list[Finding] = field(default_factory=list)
    frameworks: dict[str, str] = field(default_factory=dict)
    providers: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
def _chain(node: ast.AST) -> str:
    """Dotted name for an attribute/name expression, or '' if not one."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        inner = _chain(node.func)
        if inner:
            parts.append(inner)
    else:
        return ""
    return ".".join(reversed(parts))


def _suffix_match(chain: str, pattern: str) -> bool:
    return chain == pattern or chain.endswith("." + pattern)


def _const_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _provider_for_model(model: str) -> str | None:
    for prefix, provider in sig.MODEL_PREFIXES.items():
        if model.startswith(prefix):
            return provider
    return None


class _Walker(ast.NodeVisitor):
    def __init__(self, report: FileReport, module: str,
                 known_stores: dict[str, str] | None = None):
        self.r = report
        self.module = module
        self.stack: list[str] = []          # enclosing class/function names
        self.class_stack: list[str] = []    # enclosing class names only
        self.class_has_llm: dict[str, list[Finding]] = {}
        self.class_has_retrieval: dict[str, list[Finding]] = {}
        self.prompt_names: set[str] = set()
        # Variable -> vector_store key. Seeded with stores found elsewhere in the
        # project so a retrieval call can be linked to a store defined in another
        # module; a same-file definition overwrites the seed.
        self.store_vars: dict[str, str] = dict(known_stores or {})

    # -- context ---------------------------------------------------------
    @property
    def scope(self) -> str | None:
        return ".".join(self.stack) if self.stack else None

    @property
    def enclosing_class(self) -> str | None:
        """Innermost enclosing class, or None inside a bare function.

        Distinct from stack[0], which may be a module-level function.
        """
        return self.class_stack[-1] if self.class_stack else None

    def qualname(self, name: str) -> str:
        return ".".join([self.module] + self.stack + [name])

    def add(self, **kw) -> Finding:
        f = Finding(file=self.r.path, scope=self.scope, **kw)
        self.r.findings.append(f)
        return f

    # -- imports ----------------------------------------------------------
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self._register_module(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self._register_module(node.module)
        self.generic_visit(node)

    def _register_module(self, dotted: str):
        root = dotted.split(".")[0]
        if root in sig.FRAMEWORKS:
            self.r.frameworks[sig.FRAMEWORKS[root]] = root
        if root in sig.PROVIDER_MODULES:
            self.r.providers.add(sig.PROVIDER_MODULES[root])

    # -- assignments -------------------------------------------------------
    def visit_Assign(self, node: ast.Assign):
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for name in targets:
            self._maybe_prompt(name, node)
            self._maybe_model(name, node)
            self._maybe_tool_schema(name, node)
        self._maybe_store(targets, node.value, node)
        self._maybe_instance(targets, node.value, node)
        self.generic_visit(node)

    def _maybe_prompt(self, name: str, node: ast.Assign):
        text = _const_str(node.value)
        if text is None or len(text) < 24:
            return
        lowered = name.lower()
        by_name = any(h in lowered for h in sig.PROMPT_NAME_HINTS)
        by_shape = bool(PLACEHOLDER.search(text)) and ("\n" in text or len(text) > 120)
        if not (by_name or by_shape):
            return
        self.prompt_names.add(name)
        variables = sorted(set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", text)))
        self.add(kind="prompt", key=f"{self.r.path}:{name}", name=name,
                 line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                 symbol=self.qualname(name),
                 confidence=sig.CONF_PROMPT_BY_NAME if by_name else sig.CONF_PROMPT_BY_SHAPE,
                 data={"template": text, "variables": variables})

    def _maybe_model(self, name: str, node: ast.Assign):
        text = _const_str(node.value)
        if not text:
            return
        provider = _provider_for_model(text)
        if not provider:
            return
        self.add(kind="model", key=f"model:{text}", name=name, line=node.lineno,
                 end_line=getattr(node, "end_lineno", node.lineno),
                 symbol=self.qualname(name), confidence=0.95,
                 data={"provider": provider, "model": text, "const": name})

    def _maybe_tool_schema(self, name: str, node: ast.Assign):
        """A list of dict literals carrying a name plus a parameter schema."""
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            return
        for element in node.value.elts:
            if not isinstance(element, ast.Dict):
                continue
            keys = {_const_str(k) for k in element.keys}
            if not any(required <= keys for required in sig.TOOL_SCHEMA_KEYS):
                continue
            entry = {_const_str(k): v for k, v in zip(element.keys, element.values)}
            tool_name = _const_str(entry.get("name")) or name
            description = _const_str(entry.get("description"))
            schema_node = entry.get("input_schema") or entry.get("parameters")
            self.add(kind="tool", key=f"tool:{tool_name}", name=tool_name,
                     line=element.lineno, end_line=getattr(element, "end_lineno", element.lineno),
                     symbol=self.qualname(name), confidence=0.93,
                     data={"description": description, "declared_in": name,
                           "schema": _literal(schema_node)})

    def _maybe_instance(self, targets: list[str], value: ast.AST, node: ast.Assign):
        """`supervisor = SupervisorAgent()` -- binds a variable to a class so a
        later `supervisor.decide(...)` can be attributed to that class."""
        if not (isinstance(value, ast.Call) and targets):
            return
        chain = _chain(value.func)
        cls = chain.rsplit(".", 1)[-1]
        if not (cls[:1].isupper() and cls.isidentifier()):
            return
        for target in targets:
            self.add(kind="instance", key=f"instance:{self.r.path}:{target}", name=target,
                     line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                     symbol=self.qualname(target), confidence=0.9,
                     data={"class": cls})

    def _maybe_store(self, targets: list[str], value: ast.AST, node: ast.Assign):
        if not isinstance(value, ast.Call):
            return
        chain = _chain(value.func)
        for pattern, provider, confidence in sig.VECTOR_STORES:
            if not _suffix_match(chain, pattern):
                continue
            label = targets[0] if targets else pattern
            key = f"store:{self.r.path}:{label}"
            # `collection = client.get_or_create_collection(...)` -- the client is
            # plumbing, the collection is the actual store. Keep the narrower one.
            receiver = chain.rsplit(".", 1)[0] if "." in chain else ""
            parent = self.store_vars.get(receiver.split(".")[-1])
            if parent:
                self.r.findings = [f for f in self.r.findings
                                   if not (f.kind == "vector_store" and f.key == parent)]
            config = {}
            for kw in ("name", "collection_name", "index_name", "index", "namespace"):
                arg = _kwarg(value, kw)
                if _const_str(arg) is not None:
                    config["index" if kw != "namespace" else "namespace"] = _const_str(arg)
            self.add(kind="vector_store", key=key, name=label, line=node.lineno,
                     end_line=getattr(node, "end_lineno", node.lineno),
                     symbol=self.qualname(label), confidence=confidence,
                     data={"provider": provider, "config": config, "call": chain})
            for t in targets:
                self.store_vars[t] = key
            return

    # -- definitions -------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef):
        self.stack.append(node.name)
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()
        self.stack.pop()

        llm_calls = self.class_has_llm.pop(node.name, [])
        retrievals = self.class_has_retrieval.pop(node.name, [])
        doc = ast.get_docstring(node)
        refs = sorted(_names_in(node))

        if llm_calls:
            self.add(kind="agent", key=f"agent:{self.module}.{node.name}", name=node.name,
                     line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                     symbol=f"{self.module}.{node.name}", confidence=sig.CONF_AGENT_CLASS,
                     data={"docstring": doc, "llm_calls": [c.key for c in llm_calls],
                           "references": refs, "attr_calls": sorted(_attr_calls(node)),
                           "evidence": "class contains an LLM invocation"})
        elif retrievals:
            self.add(kind="retriever", key=f"retriever:{self.module}.{node.name}",
                     name=node.name, line=node.lineno,
                     end_line=getattr(node, "end_lineno", node.lineno),
                     symbol=f"{self.module}.{node.name}", confidence=sig.CONF_RETRIEVER_CLASS,
                     data={"docstring": doc, "stores": [c.data.get("store") for c in retrievals],
                           "references": refs,
                           "evidence": "class contains a retrieval call"})

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._function(node)

    def _function(self, node):
        for dec in node.decorator_list:
            chain = _chain(dec.func if isinstance(dec, ast.Call) else dec)
            leaf = chain.rsplit(".", 1)[-1]
            if leaf in sig.TOOL_DECORATORS:
                self.add(kind="tool", key=f"tool:{node.name}", name=node.name,
                         line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                         symbol=self.qualname(node.name),
                         confidence=sig.TOOL_DECORATORS[leaf],
                         data={"description": ast.get_docstring(node), "decorator": chain})
            elif leaf in sig.ROUTE_DECORATORS and "." in chain:
                route = None
                if isinstance(dec, ast.Call) and dec.args:
                    route = _const_str(dec.args[0])
                self.add(kind="entrypoint", key=f"entry:{self.module}.{node.name}",
                         name=node.name, line=node.lineno,
                         end_line=getattr(node, "end_lineno", node.lineno),
                         symbol=self.qualname(node.name), confidence=sig.CONF_ENTRYPOINT,
                         data={"route": route, "verb": leaf, "kind": "http",
                               "flow": _flow(node)})

        if not self.stack and node.name == "main":
            self.add(kind="entrypoint", key=f"entry:{self.module}.main", name="main",
                     line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                     symbol=self.qualname("main"), confidence=0.7,
                     data={"kind": "cli", "flow": _flow(node)})

        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    # -- calls -------------------------------------------------------------
    def visit_Call(self, node: ast.Call):
        chain = _chain(node.func)

        for pattern, provider, confidence in sig.LLM_CALLS:
            if _suffix_match(chain, pattern):
                self._llm_call(node, chain, provider, confidence)
                break
        else:
            self._maybe_retrieval(node, chain)

        self.generic_visit(node)

    def _llm_call(self, node: ast.Call, chain: str, provider: str, confidence: float):
        model_arg = _kwarg(node, "model")
        model_literal = _const_str(model_arg)
        model_const = model_arg.id if isinstance(model_arg, ast.Name) else None

        referenced = _names_in(node)
        prompts = sorted(referenced & self.prompt_names)
        tools_arg = _kwarg(node, "tools")
        tool_refs = sorted(_names_in(tools_arg)) if tools_arg is not None else []

        params = {}
        for kw in ("max_tokens", "temperature", "top_p"):
            arg = _kwarg(node, kw)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                params[kw] = arg.value

        finding = self.add(
            kind="llm", key=f"llm:{self.r.path}:{node.lineno}",
            name=f"{self.enclosing_class or self.module}.{chain.rsplit('.', 1)[-1]}",
            line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
            symbol=self.qualname(chain.rsplit(".", 1)[-1]), confidence=confidence,
            data={"provider": provider, "call": chain, "model": model_literal,
                  "model_const": model_const, "prompts": prompts,
                  "tool_refs": tool_refs, "parameters": params},
        )
        if self.enclosing_class:
            self.class_has_llm.setdefault(self.enclosing_class, []).append(finding)

    def _maybe_retrieval(self, node: ast.Call, chain: str):
        if not chain:
            return
        leaf = chain.rsplit(".", 1)[-1]
        receiver = chain.rsplit(".", 1)[0] if "." in chain else ""
        base = receiver.split(".")[-1] if receiver else ""
        known_store = self.store_vars.get(base) or self.store_vars.get(receiver)

        for pattern, confidence in sig.RETRIEVAL_CALLS:
            if leaf != pattern:
                continue
            # generic verbs only count when the receiver is a known store
            if pattern in ("query", "search") and not known_store:
                return
            finding = self.add(
                kind="retrieval", key=f"retrieval:{self.r.path}:{node.lineno}",
                name=leaf, line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                symbol=self.qualname(leaf),
                confidence=confidence if known_store else confidence - 0.1,
                data={"call": chain, "store": known_store, "receiver": receiver})
            if self.enclosing_class:
                self.class_has_retrieval.setdefault(self.enclosing_class, []).append(finding)
            return


def _attr_calls(node: ast.AST) -> set[str]:
    """Receiver names of attribute calls made anywhere inside `node`."""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            base = _chain(sub.func)
            if "." in base:
                out.add(base.rsplit(".", 1)[0].split(".")[-1])
    return out


def _flow(func: ast.AST) -> list[dict]:
    """Order and dependencies of the attribute calls in a function body.

    For `route = supervisor.decide(q)` followed by
    `answer = answerer.answer(q, route)`, this reports that the second call
    consumed a value the first produced -- which is what distinguishes a real
    data-flow edge from two calls that merely sit in the same function.
    """
    produced: dict[str, str] = {}      # local variable -> receiver that produced it
    steps: list[dict] = []
    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Assign):
            continue
        call = stmt.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        chain = _chain(call.func)
        if "." not in chain:
            continue
        receiver = chain.rsplit(".", 1)[0].split(".")[-1]
        consumed = sorted({produced[n] for n in _names_in(call) if n in produced})
        targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        steps.append({"receiver": receiver, "method": chain.rsplit(".", 1)[-1],
                      "assigns": targets, "consumes": consumed, "line": stmt.lineno})
        for t in targets:
            produced[t] = receiver
    steps.sort(key=lambda s: s["line"])
    return steps


def _literal(node) -> object:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


# ---------------------------------------------------------------------------
def analyze_source(source: str, path: str, module: str | None = None,
                   known_stores: dict[str, str] | None = None) -> FileReport:
    report = FileReport(path=path)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        report.notes.append(f"{path}: skipped, syntax error at line {exc.lineno}")
        return report
    module = module or Path(path).with_suffix("").as_posix().replace("/", ".")
    _Walker(report, module, known_stores).visit(tree)
    return report


def iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def analyze_path(root: str | Path) -> list[FileReport]:
    """Analyze a file or a project tree.

    A project is walked twice: the first pass finds vector stores, the second
    re-analyzes with those names in scope so a retrieval call can be linked to a
    store its module merely imports. Names are matched bare, so two stores
    sharing a variable name in different modules resolve to whichever the second
    pass sees last -- the resulting edge carries the rule's confidence, not
    certainty.
    """
    root = Path(root)
    if root.is_file():
        return [analyze_source(root.read_text(), root.name)]

    sources: list[tuple[str, str, str]] = []
    for path in iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        module = rel[:-3].replace("/", ".").removesuffix(".__init__")
        try:
            sources.append((rel, module, path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError) as exc:
            sources.append((rel, module, None))
            _UNREADABLE[rel] = str(exc)

    known: dict[str, str] = {}
    for rel, module, text in sources:
        if text is None:
            continue
        for f in analyze_source(text, rel, module).findings:
            if f.kind == "vector_store":
                known[f.name] = f.key

    reports = []
    for rel, module, text in sources:
        if text is None:
            reports.append(FileReport(path=rel,
                                      notes=[f"{rel}: unreadable ({_UNREADABLE[rel]})"]))
            continue
        reports.append(analyze_source(text, rel, module, known_stores=known))
    return reports


_UNREADABLE: dict[str, str] = {}
