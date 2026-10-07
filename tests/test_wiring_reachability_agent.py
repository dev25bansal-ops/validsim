"""Capability-wiring gate: a shipped capability must have a production caller.

Motivation
----------
Seven capabilities in this repository were simultaneously *implemented*,
*unit-tested*, *documented* and *never called by anything that runs*:
``config_loader``, ``llm_generator``, ``sim/shadow.py``, ``notify/``,
``engine/trends.py``, ``detect_anomalies`` and ``resolve_asset_path``. Each had
a green test suite, which is exactly why the gap survived: a test proves a
function works, never that the product uses it.

This module closes that class of gap going forward. It is a *regression gate*,
not a claim about today's tree. A capability may sit in :data:`UNWIRED_BY_DESIGN`
only while the repository honestly documents it as not-wired; the moment such a
capability is (or is re-documented as) a shipped feature, the allowance must be
deleted and the capability wired -- and then this test fails, which is the point.

Method
------
Reachability is decided by parsing the production tree with :mod:`ast`. A symbol
counts as *called* only if it appears in a load-bearing position: an
``ast.Name``/``ast.Attribute`` in **Load** context, outside import statements,
``__all__`` literals and docstrings, in at least one production module that is
not a pure re-export shim.

Four legitimate call shapes are recognised explicitly, because omitting them
would make the gate cry wolf across the whole HTTP/CLI surface:

* **Own-module calls.** A factory that builds a class in its own module
  (``create_app`` -> ``app = create_app()``; ``create_job_queue`` ->
  ``RedisJobQueue()``) is a genuine production call site.
* **Decorator registration.** ``@router.get``, ``@app.post``, ``@cli.command``
  and friends hand the function to FastAPI/Typer, which invokes it by identity;
  the decorator *is* the call site.
* **Type use in an entry point.** A symbol named in an entry-point module's
  annotations is load-bearing, because FastAPI builds request schemas and
  resolves ``Depends`` types from those annotations at startup.
* **Reachability is transitive.** A capability whose every call site lives in
  code that is itself unreachable is itself unreachable, so reachability is
  computed as a fixed point over the call graph rather than one hop.

Package ``__init__.py`` files that only re-export are excluded as call sites: a
re-export proves the symbol *exists*, not that it is *used*, so counting them
would make every orphan look wired.

Accuracy is self-checked: the analyzer's answers are pinned against capabilities
whose wiring is known by hand (the ``test_analyzer_*`` tests below), so a
refactor of the analyzer cannot silently turn the gate into a no-op.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_ROOT = _REPO_ROOT / "validsim"

# ---------------------------------------------------------------------------
# The allow-list. ``(module, capability) -> why it may be unwired``
#
# An entry is a *claim about the product*, not a licence to add dead code. Every
# reason below names why the capability is not shipped, and the tests at the end
# of the file fail as soon as the tree stops matching the claim.
# ---------------------------------------------------------------------------

#: Capabilities that are genuinely not on the shipped execution path today.
UNWIRED_BY_DESIGN: dict[tuple[str, str], str] = {
    (
        "validsim.config_loader",
        "load_config_file",
    ): "optional config-file override support; no production importer",
    (
        "validsim.config_loader",
        "load_default_config",
    ): "optional config-file override support; no production importer",
    (
        "validsim.config_loader",
        "discover_config_file",
    ): "optional config-file override support; no production importer",
    ("validsim.engine.trends", "compute_trends"): "trend reporting helper; no production importer",
    (
        "validsim.engine.trends",
        "failure_mode_trends",
    ): "trend reporting helper; no production importer",
    ("validsim.engine.trends", "TrendSummary"): "result type of the unwired compute_trends",
    (
        "validsim.engine.anomaly",
        "detect_anomalies",
    ): "opt-in analysis helper; no production importer",
    ("validsim.engine.anomaly", "Anomaly"): "result type of the unwired detect_anomalies",
    (
        "validsim.engine.benchmark",
        "compare_scorecards",
    ): "the /compare endpoint uses regression.compare, not this module",
    (
        "validsim.engine.benchmark",
        "BenchmarkResult",
    ): "result type of the unwired compare_scorecards",
    (
        "validsim.engine.benchmark",
        "MetricComparison",
    ): "type of the unwired compare_scorecards comparisons",
    (
        "validsim.engine.pdf",
        "render_scorecard_pdf",
    ): "only scorecard_pdf_bytes is served; the path-writing variant has no CLI or API caller",
    (
        "validsim.sim.shadow",
        "ShadowRunner",
    ): "shadow-run harness; rehearsed by tests, never invoked by production",
    ("validsim.sim.shadow", "ShadowReport"): "result type of the unwired ShadowRunner",
    (
        "validsim.sim.shadow",
        "reference_contract_cases",
    ): "shadow-run harness helper; no production importer",
    (
        "validsim.sim.shadow",
        "contract_violations_for",
    ): "shadow-run harness helper; no production importer",
    ("validsim.sim.shadow", "ContractCase"): "type of the unwired shadow contract cases",
    (
        "validsim.notify.dispatcher",
        "WebhookDispatcher",
    ): "webhook dispatch is not mounted on the API, CLI or job worker",
    (
        "validsim.notify.dispatcher",
        "DeliveryResult",
    ): "result type of the unwired WebhookDispatcher",
    (
        "validsim.notify.dispatcher",
        "severity_from_scorecard",
    ): "webhook dispatch is not mounted on the API, CLI or job worker",
    (
        "validsim.notify.dispatcher",
        "format_slack_blocks",
    ): "webhook dispatch is not mounted on the API, CLI or job worker",
    (
        "validsim.notify.email",
        "EmailNotifier",
    ): "SMTP email is not mounted on the API, CLI or job worker",
    ("validsim.notify.email", "EmailDelivery"): "result type of the unwired EmailNotifier",
    (
        "validsim.notify.email",
        "scorecard_email_body",
    ): "SMTP email is not mounted on the API, CLI or job worker",
    (
        "validsim.notify.email",
        "scorecard_text_body",
    ): "SMTP email is not mounted on the API, CLI or job worker",
    (
        "validsim.notify.email",
        "SmtpSettings",
    ): "SMTP email is not mounted on the API, CLI or job worker",
    (
        "validsim.scenarios.llm_generator",
        "LLMScenarioGenerator",
    ): "LLM scenario generation is never selected by the pipeline or CLI",
    (
        "validsim.scenarios.llm_generator",
        "OpenAICompatibleProvider",
    ): "LLM scenario generation is never selected by the pipeline or CLI",
    (
        "validsim.scenarios.llm_generator",
        "create_scenario_generator",
    ): "LLM scenario generation is never selected by the pipeline or CLI",
    (
        "validsim.scenarios.llm_generator",
        "build_scenario_prompt",
    ): "LLM scenario generation is never selected by the pipeline or CLI",
    (
        "validsim.scenarios.llm_generator",
        "current_scenario_backend",
    ): "LLM scenario generation is never selected by the pipeline or CLI",
    (
        "validsim.scenarios.llm_generator",
        "llm_enabled",
    ): "LLM scenario generation is never selected by the pipeline or CLI",
    (
        "validsim.scenarios.llm_generator",
        "ScenarioProvider",
    ): "protocol of the unwired LLM scenario generator",
    (
        "validsim.scenarios.llm_generator",
        "ScenarioParseError",
    ): "error type of the unwired LLM scenario generator",
    (
        "validsim.scenarios.llm_generator",
        "ScenarioProviderError",
    ): "error type of the unwired LLM scenario generator",
    ("validsim.config", "resolve_asset_path"): "asset path helper; no production caller",
    (
        "validsim.project_config",
        "resolve_config",
    ): "module docstring: not yet wired into production",
    ("validsim.project_config", "config_to_env"): "module docstring: not yet wired into production",
    (
        "validsim.project_config",
        "effective_to_env",
    ): "module docstring: not yet wired into production",
    (
        "validsim.project_config",
        "known_env_keys",
    ): "module docstring: not yet wired into production",
    ("validsim.project_config", "classify_key"): "module docstring: not yet wired into production",
    ("validsim.project_config", "mask_secret"): "module docstring: not yet wired into production",
    ("validsim.project_config", "redact_value"): "module docstring: not yet wired into production",
    (
        "validsim.store.memory",
        "DuplicateRunError",
    ): "raised on duplicate save; exported as public API for embedders",
    (
        "validsim.engine._coerce",
        "as_int",
    ): (
        "only as_int callers are engine/anomaly.py and engine/trends.py, which "
        "are themselves unwired; as_float is wired via engine/pdf.py"
    ),
    (
        "validsim.jobs.router",
        "EnqueueJobRequest",
    ): "body model of the unwired enqueue route; no production importer",
    (
        "validsim.notify.dispatcher",
        "notify_run_completion",
    ): "the notify entry point is exported but not called from the pipeline, CLI or worker yet",
    (
        "validsim.notify.config",
        "notify_config_from_env",
    ): "called only by the unwired notify_run_completion",
    ("validsim.notify.config", "NotifyConfig"): "result type of the unwired notify_config_from_env",
    ("validsim.notify.config", "HookSpec"): "element type of the unwired NotifyConfig",
    (
        "validsim.config_loader",
        "ConfigFileError",
    ): "raised by the unwired config_loader bridge; no production importer",
    (
        "validsim.project_config",
        "ConfigFileError",
    ): "raised by the unwired project_config module; no production importer",
    (
        "validsim.project_config",
        "discover_config_file",
    ): (
        "second, unrelated discover_config_file; only reachable from the "
        "unwired project_config module"
    ),
    # -- api/store_stats.py: store_totals, latest_composite and their result
    # -- type StoreTotals are now wired via api/metrics.render_metrics, so they
    # -- are intentionally absent below. The list/dashboard-history readers that
    # -- would replace them are still not routed here.
    (
        "validsim.api.store_stats",
        "run_summaries",
    ): (
        "O(1) run summaries; no production caller yet -- exercised only by "
        "tests, while the list/dashboard-history readers it would replace are "
        "still not routed here"
    ),
    (
        "validsim.api.store_stats",
        "strategy_for",
    ): (
        "pick query strategy; no production caller yet -- exercised only by "
        "tests, not by the store_stats readers it reports on"
    ),
}

#: The seven capabilities named in the module docstring. The analyzer must
#: still agree they are uncalled; if it ever stops agreeing, the analyzer broke
#: (or a capability got wired -- in which case the allow-list must be updated).
CALIBRATION_SET = {
    "load_default_config": "validsim.config_loader",
    "detect_anomalies": "validsim.engine.anomaly",
    "compute_trends": "validsim.engine.trends",
    "ShadowRunner": "validsim.sim.shadow",
    "WebhookDispatcher": "validsim.notify.dispatcher",
    "EmailNotifier": "validsim.notify.email",
    "resolve_asset_path": "validsim.config",
}

#: Modules that are process entry points: launched, never imported by other
#: production code, yet fully shipped. Everything transitively imported from one
#: of these is reachable; anything else is a candidate orphan.
ENTRYPOINT_MODULES = frozenset(
    {
        "validsim.cli",
        "validsim.api.main",
    }
)

#: Package ``__init__.py`` modules. These are pure re-export surfaces and are
#: deliberately **not** treated as reachability roots: no production module does
#: ``from validsim.engine import ...`` (the tree consistently imports submodules
#: directly), so seeding from them would make every re-exported orphan look
#: reachable. They are only reached as a side effect of importing a submodule.
PACKAGE_ROOTS = frozenset(
    {
        "validsim",
        "validsim.api",
        "validsim.engine",
        "validsim.jobs",
        "validsim.notify",
        "validsim.scenarios",
        "validsim.sim",
        "validsim.store",
    }
)

#: Capability names that legitimately have no caller, with the reason. These are
#: *data* (module-level constants) and *exported objects* rather than
#: capabilities: the constant is the thing, the name is the handle.
NON_CAPABILITY_EXPORTS = frozenset(
    {
        "__version__",
        "app",  # module-level ASGI instance
        "router",  # APIRouter, mounted by create_app
        "ENV_ENABLED",
        "ENV_FORMATS",
        "ENV_MIN_SEVERITY",
        "ENV_SECRETS",
        "ENV_URLS",
        "HookFormat",
        "Severity",
        "ADVERSARIAL_CATEGORIES",
        "FAILURE_MODES",
        "WEB_DIR",
        "INDEX_PATH",
        "DEFAULT_CONFIG_NAMES",
        "CONFIG_ENV",
    }
)

_ROUTE_DECORATORS = frozenset(
    {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "trace",
        "websocket",
        "command",
        "callback",
        "api_route",
        "route",
    }
)


# ---------------------------------------------------------------------------
# AST analysis
# ---------------------------------------------------------------------------


def _production_files() -> list[Path]:
    return sorted(p for p in _PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _dotted_name(path: Path) -> str:
    module = path.relative_to(_REPO_ROOT).as_posix()[: -len(".py")]
    return module.replace("/", ".").removesuffix(".__init__")


def _is_reexport_shim(tree: ast.Module) -> bool:
    """True when every top-level statement is an import or a dunder assignment."""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in ("__all__", "annotations"):
                continue
            return False
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        return False
    return True


def _annotation_node_ids(tree: ast.Module) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            out.update(id(sub) for sub in ast.walk(node.annotation))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                out.update(id(sub) for sub in ast.walk(node.returns))
    return out


def _load_bearing_refs(
    tree: ast.Module, *, include_annotations: bool = False
) -> dict[str, set[int]]:
    """Map ``name -> {lineno}`` for load-bearing uses in one module.

    Excludes import aliases (an import proves availability, not use), ``__all__``
    literals and docstrings -- all of which otherwise make dead code look busy.
    Annotations are excluded unless ``include_annotations`` is set, because a
    type mention is a weaker signal than a call.
    """
    annotations = _annotation_node_ids(tree)
    refs: dict[str, set[int]] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(child, ast.Assign):
                target = child.targets[0]
                if isinstance(target, ast.Name) and target.id == "__all__":
                    continue
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                continue  # docstring
            if id(child) not in annotations or include_annotations:
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                    refs.setdefault(child.id, set()).add(child.lineno)
                elif isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
                    refs.setdefault(child.attr, set()).add(child.lineno)
            visit(child)

    visit(tree)
    return refs


def _is_route_decorated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when FastAPI/Typer registers the function through a decorator."""
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
            if decorator.func.attr in _ROUTE_DECORATORS:
                return True
        elif isinstance(decorator, ast.Attribute) and decorator.attr in _ROUTE_DECORATORS:
            return True
        elif isinstance(decorator, ast.Name) and decorator.id in _ROUTE_DECORATORS:
            return True
    return False


_FILES = _production_files()
_TREES = {p: ast.parse(p.read_text(encoding="utf-8")) for p in _FILES}
_REL = {p: p.relative_to(_REPO_ROOT).as_posix() for p in _FILES}
_SHIMS = {p for p, tree in _TREES.items() if _is_reexport_shim(tree)}
_REFS = {p: _load_bearing_refs(tree) for p, tree in _TREES.items()}
_ANN_REFS = {p: _load_bearing_refs(tree, include_annotations=True) for p, tree in _TREES.items()}

#: ``file -> {dotted module names it imports}`` (intra-package edges only).
#: Walks the whole tree because deferred in-function imports are load-bearing
#: edges too (see :data:`_BINDINGS`).
_MODULE_IMPORTS: dict[Path, set[str]] = {}
for _path, _tree in _TREES.items():
    _imports: set[str] = set()
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.ImportFrom):
            continue
        if _node.level:
            _parts = _REL[_path].split("/")[:-1]
            _dotted = ".".join(_parts[: len(_parts) - _node.level + 1]) + (
                f".{_node.module}" if _node.module else ""
            )
        elif _node.module and _node.module.startswith("validsim"):
            _dotted = _node.module
        else:
            continue
        _imports.add(_dotted.lstrip(".").removesuffix(".__init__"))
    _MODULE_IMPORTS[_path] = _imports


def _reachable_modules() -> set[Path]:
    """Modules transitively reachable from a real entry point.

    A module nothing reaches is unreachable, and so is every symbol whose only
    caller lives inside it. Without this second-order step the gate would pass
    ``JsonLogFormatter``, which is instantiated only by a helper in the same
    orphan module that calls it.
    """
    by_name = {_dotted_name(p): p for p in _FILES}
    reached: set[Path] = {by_name[m] for m in ENTRYPOINT_MODULES if m in by_name}
    stack = list(reached)
    while stack:
        current = stack.pop()
        for imported in _MODULE_IMPORTS.get(current, ()):
            # ``from pkg import submodule`` makes the submodule reachable, and
            # importing any part of a package executes its __init__.
            for candidate in (imported, f"{imported}.__init__"):
                target = by_name.get(candidate)
                if target is not None and target not in reached:
                    reached.add(target)
                    stack.append(target)
    return reached


_REACHABLE_MODULES = _reachable_modules()

#: ``file -> {local name -> (module, symbol) it is bound to}`` for intra-package
#: imports. This is what stops a same-named symbol in an unrelated module from
#: vouching for a dead one: the tree has two ``redact_value`` functions, two
#: ``discover_config_file`` functions and two ``ConfigFileError`` classes, and a
#: name-keyed scan would credit ``validsim.logging``'s use of its own
#: ``redact_value`` to the unrelated ``validsim.project_config`` one.
_BINDINGS: dict[Path, dict[str, tuple[str, str]]] = {}
for _path, _tree in _TREES.items():
    _local: dict[str, tuple[str, str]] = {}
    # Scan the whole tree, not just module level: production code deliberately
    # defers imports into function bodies (``from validsim.api.dashboard import
    # mount_dashboard`` sits inside ``create_app``) to keep app construction
    # cheap, and a module-level-only scan would miss every one of them.
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.ImportFrom):
            continue
        if _node.level:
            _parts = _REL[_path].split("/")[:-1]
            _base = ".".join(_parts[: len(_parts) - _node.level + 1])
        elif _node.module and _node.module.startswith("validsim"):
            _base = _node.module
        else:
            continue
        for _alias in _node.names:
            if _alias.name == "*":
                continue
            _local[_alias.asname or _alias.name] = (
                _base.lstrip(".").removesuffix(".__init__"),
                _alias.name,
            )
    _BINDINGS[_path] = _local

#: ``(module, symbol) -> defining file`` for public top-level defs.
_SYMBOL_HOME: dict[tuple[str, str], Path] = {}
#: ``(module, name) -> defining file`` for module-level *data* (annotated
#: assignments such as ``TERMINAL_STATUSES: frozenset[JobStatus] = ...``).
#: Tracked separately because a constant is "used" by being imported and read,
#: never called, so it needs a different admission rule than a function.
_DATA_HOME: dict[tuple[str, str], Path] = {}
for _path, _tree in _TREES.items():
    for _node in _tree.body:
        if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _node.name.startswith("_"):
                _SYMBOL_HOME.setdefault((_dotted_name(_path), _node.name), _path)
        elif isinstance(_node, ast.AnnAssign) and isinstance(_node.target, ast.Name):
            if not _node.target.id.startswith("_"):
                _DATA_HOME.setdefault((_dotted_name(_path), _node.target.id), _path)


def _in_signature_span(definition: ast.AST, lineno: int) -> bool:
    """True when ``lineno`` is inside ``definition``'s decorators or signature.

    A ``def f(x=f)``-style self-mention is a forward reference used for typing or
    a default value, resolved when the ``def`` statement executes -- it is not a
    call to ``f``. Only the statement's own span (decorators included) qualifies;
    the function *body* does not, so a genuine recursive or sibling call from
    inside the body is still counted.
    """
    if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    first = min([definition.lineno] + [d.lineno for d in definition.decorator_list])
    # ``args``/``returns`` annotations live in the statement span, which ends at
    # the first body statement. ``end_lineno`` on a def covers the whole function,
    # so the body start is located explicitly instead.
    body_start = definition.body[0].lineno if definition.body else definition.end_lineno
    return first <= lineno < (body_start or definition.end_lineno or lineno + 1)


#: ``(module, symbol) -> [call-site "file:line", ...]``
_CALL_SITES: dict[tuple[str, str], list[str]] = {}
for _key, _home in _SYMBOL_HOME.items():
    _module, _sym = _key
    _sites: list[str] = []
    for _path, _tree in _TREES.items():
        if _path not in _REACHABLE_MODULES:
            continue  # dead module: a call site here cannot be reached
        _definition = next(
            (
                n
                for n in _tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.name == _sym
            ),
            None,
        )
        _is_home = _path == _home
        if (
            _definition is not None
            and isinstance(_definition, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _is_route_decorated(_definition)
        ):
            # FastAPI/Typer invokes this by identity through its registry, so the
            # decorator *is* the call site -- even though the decorator sits on
            # the definition itself, in the function's own module.
            _sites.append(f"{_REL[_path]}:{_definition.lineno} (decorator-registered)")
            continue
        # An imported binding points at a *specific* module's symbol; an
        # unimported bare name is only a call site for a same-module definition.
        _binding = _BINDINGS[_path].get(_sym)
        if _binding is not None and _binding != _key:
            continue  # this name refers to some other module's symbol
        if _binding is None and not _is_home:
            continue  # an unimported bare name elsewhere is a different symbol
        for _lineno in sorted(_REFS[_path].get(_sym, ())):
            if _is_home and _definition is not None and _in_signature_span(_definition, _lineno):
                # A mention inside the def's own decorators/signature is a
                # *forward reference* (an annotation or default naming the
                # symbol being defined), not a call. A mention anywhere else in
                # the module is a real call even when it appears earlier in the
                # file than the definition: the module is fully executed before
                # any call, so line 421 can legitimately call a function defined
                # at line 518. Only the signature span is skipped.
                continue
            _sites.append(f"{_REL[_path]}:{_lineno}")
        if _dotted_name(_path) in ENTRYPOINT_MODULES:
            # An annotation-only mention inside a route signature is load-bearing:
            # FastAPI builds a request model / resolves ``Depends`` from it at
            # startup, so a pydantic model used only as a route body type is
            # genuinely used.
            for _a in sorted(_ANN_REFS[_path].get(_sym, set())):
                if _a in _REFS[_path].get(_sym, set()):
                    continue  # already counted as a real reference
                if _is_home and _definition is not None and _in_signature_span(_definition, _a):
                    continue  # a self-annotation inside its own signature
                _sites.append(f"{_REL[_path]}:{_a} (annotation in entry point)")
    _CALL_SITES[_key] = sorted(set(_sites))


def _wired(module: str, symbol: str) -> list[str]:
    """Production call sites for ``module.symbol``; empty when unreachable."""
    return _CALL_SITES.get((module, symbol), [])


def _definition_of(home: Path, symbol: str) -> ast.stmt | None:
    """The top-level ``def``/``class``/``AnnAssign`` node for ``symbol``."""
    for node in _TREES[home].body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == symbol
        ):
            return node
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == symbol:
                return node
    return None


def _all_public_capabilities() -> dict[tuple[str, str], Path]:
    return dict(_SYMBOL_HOME)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_analyzer_reads_the_whole_production_tree() -> None:
    """The analyzer must analyse the real tree, not an empty glob."""
    assert len(_FILES) >= 30, (
        f"only {len(_FILES)} production files found under {_PACKAGE_ROOT}; the "
        "reachability analyzer is running against a partial tree"
    )
    for expected in (
        "validsim/engine/scorecard.py",
        "validsim/cli.py",
        "validsim/api/main.py",
    ):
        assert expected in _REL.values(), f"{expected} missing from the parsed tree"


def test_analyzer_still_flags_the_documented_unwired_capabilities() -> None:
    """Calibration: the seven known orphans must still read as uncalled.

    A reachability analyzer that reports everything as dead is as useless as one
    that reports everything as wired, so the negative direction is pinned too.
    """
    now_wired = {s: _wired(m, s) for s, m in CALIBRATION_SET.items() if _wired(m, s)}
    assert not now_wired, (
        "these calibration capabilities now have production call sites, so the "
        f"analyzer or the tree changed: {now_wired}. If they were deliberately "
        "wired, drop them from CALIBRATION_SET and UNWIRED_BY_DESIGN."
    )


@pytest.mark.parametrize(
    ("module", "symbol"),
    [
        ("validsim.sim.runner", "run_validation"),
        ("validsim.engine.scorecard", "build_scorecard"),
        ("validsim.store", "create_store"),
        ("validsim.sim", "create_backend"),
        ("validsim.engine.safety", "compute_safety"),
    ],
)
def test_analyzer_detects_capabilities_that_are_genuinely_wired(module: str, symbol: str) -> None:
    """Counter-test: the analyzer must be capable of answering 'wired'.

    Without this, an analyzer that returned "no call sites" for everything would
    still pass the calibration test above.
    """
    assert _wired(module, symbol), (
        f"expected {module}.{symbol} to have production call sites; the analyzer is under-reporting"
    )


def test_analyzer_detects_own_module_and_decorator_call_sites() -> None:
    """The two call shapes most likely to be missed by a naive scan.

    ``create_app`` is called only by ``app = create_app()`` in its own module,
    and every API/CLI entry point is invoked purely through a decorator. If
    either shape were unrecognised the gate would fire on the whole HTTP/CLI
    surface and be useless.
    """
    sites = _wired("validsim.api.main", "create_app")
    assert sites, "own-module factory call not recognised"
    assert any(s.startswith("validsim/api/main.py:") for s in sites), (
        "create_app should be reached via the module-level `app = create_app()`"
    )
    # ``main`` is Typer's entry point: launched, never imported.
    assert _wired("validsim.cli", "main"), "the CLI entry point was not recognised"
    # Route handlers are reachable only because the decorator registers them.
    assert any(
        "decorator-registered" in s for s in _wired("validsim.api.dashboard", "dashboard_summary")
    ), "FastAPI route registration was not recognised as a call site"


@pytest.mark.parametrize(("module", "symbol"), sorted(UNWIRED_BY_DESIGN))
def test_allow_listed_capability_is_genuinely_unwired(module: str, symbol: str) -> None:
    """An allow-list entry must be true: the capability really has no caller.

    This is the assertion that makes the allow-list trustworthy. If a capability
    is wired, the entry is now a lie and the test fails.
    """
    sites = _wired(module, symbol)
    assert not sites, (
        f"{module}.{symbol} has production call sites ({sites}) but is listed "
        "in UNWIRED_BY_DESIGN as unwired; remove the entry and correct the docs "
        "that describe it as not-wired"
    )


@pytest.mark.parametrize(("module", "symbol"), sorted(UNWIRED_BY_DESIGN))
def test_allow_listed_capability_still_exists_where_claimed(module: str, symbol: str) -> None:
    """The allow-list must point at the real home of the capability.

    Without this, a rename or a move would leave the gate silently protecting a
    symbol that no longer exists.
    """
    reason = UNWIRED_BY_DESIGN[(module, symbol)]
    assert isinstance(reason, str) and reason.strip(), (
        f"{module}.{symbol} is allow-listed with a malformed reason: {reason!r}"
    )
    assert (module, symbol) in _SYMBOL_HOME, (
        f"{module}.{symbol} is allow-listed but no longer exists in the production tree"
    )


def test_no_public_capability_is_left_unclassified() -> None:
    """Every public capability must be wired or explicitly allow-listed.

    This is what keeps the allow-list honest in both directions: a new module
    added with no caller and no entry here fails immediately, so unreferenced
    code cannot quietly accumulate behind a re-export shim.
    """
    unclassified = sorted(
        f"{module}.{symbol}"
        for module, symbol in _all_public_capabilities()
        if not _wired(module, symbol) and (module, symbol) not in UNWIRED_BY_DESIGN
    )
    assert not unclassified, (
        "public capabilities with no production call site and no allow-list "
        f"entry (wire them, or document them as not-wired): {unclassified}"
    )


def _export_sources(tree: ast.Module) -> dict[str, str]:
    """Map each ``__all__`` name to the module it is imported from.

    Resolved from the ``from X import a, b`` statements themselves rather than by
    name lookup, so a re-export is attributed to its real defining module even
    when two modules export the same name.
    """
    sources: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module:
            continue
        for alias in node.names:
            if alias.name != "*":
                sources[alias.asname or alias.name] = node.module
    return sources


def _is_used_as_data(module: str, name: str) -> bool:
    """True when reachable production code merely *imports* ``module.name``.

    Most symbols must be *called* to count as wired, but a module-level
    constant has no call sites by nature -- ``TERMINAL_STATUSES`` is used as
    ``if status in TERMINAL_STATUSES``, and the import is the only evidence
    there is. This admits the import for non-callable module-level data only,
    so a genuinely dead function is never rescued by a bare import.
    """
    home = _SYMBOL_HOME.get((module, name)) or _DATA_HOME.get((module, name))
    if home is None or home not in _REACHABLE_MODULES:
        return False
    if isinstance(_definition_of(home, name), (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return any(
        bindings.get(name) == (module, name)
        for path, bindings in _BINDINGS.items()
        if path in _REACHABLE_MODULES
    )


def test_every_reexported_symbol_is_either_wired_or_allow_listed() -> None:
    """Package ``__all__`` exports must not smuggle in unreferenced code.

    A re-export makes a symbol look like public API in IDEs and docs, so an
    unreachable export is the most misleading form of dead code. This closes the
    loophole of a capability that is dead but merely *not* a top-level def in its
    own module (a constant, a router object, an env-var name).
    """
    unreached: list[str] = []
    for path, tree in _TREES.items():
        if path.name != "__init__.py":
            continue
        sources = _export_sources(tree)
        for name in sorted(_exported_names(tree)):
            if name in NON_CAPABILITY_EXPORTS:
                continue
            module = sources.get(name)
            if module is None:
                # A hand-written export (a module-level object); judge it where
                # the package itself defines it.
                module = _dotted_name(path)
            key = (module, name)
            if not _wired(*key) and key not in UNWIRED_BY_DESIGN and _is_used_as_data(module, name):
                continue
            if not _wired(*key) and key not in UNWIRED_BY_DESIGN:
                unreached.append(f"{_REL[path]}:{name} (defined in {module})")
    assert not unreached, (
        f"these symbols are re-exported as public API but never called: {sorted(unreached)}"
    )


def _exported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "__all__"):
            continue
        for element in node.value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                names.add(element.value)
    return names


def test_module_level_orphans_are_declared_entry_points() -> None:
    """A module no production path reaches must be a declared entry point.

    An unreached, undeclared module is unreferenced by definition -- the
    "orphan module" form of dead code that symbol-level analysis cannot see.
    """
    known_unreachable = {
        # Package __init__ shims: reached only as a side effect of importing a
        # sibling, and never imported directly by the tree.
        "validsim.api",
        "validsim.engine",
        "validsim.jobs",
        "validsim.notify",
        "validsim.scenarios",
        # Documented as optional/unwired; see UNWIRED_BY_DESIGN.
        "validsim.config_loader",
        "validsim.engine.anomaly",
        "validsim.engine.benchmark",
        "validsim.engine.trends",
        "validsim.project_config",
        "validsim.scenarios.llm_generator",
        "validsim.sim.shadow",
        # The notify layer: exported and documented as reachable from the run
        # path, but no shipped entry point imports it yet.
        "validsim.notify.config",
        "validsim.notify.dispatcher",
        "validsim.notify.email",
        # An O(1) store-summary projection with its own test suite but not yet
        # routed to; delete this line once an endpoint calls it.
        "validsim.api.store_stats",
    }
    orphans = sorted(
        _dotted_name(path)
        for path in _FILES
        if path not in _REACHABLE_MODULES
        and _dotted_name(path) not in ENTRYPOINT_MODULES
        and _dotted_name(path) not in PACKAGE_ROOTS
        and _dotted_name(path) not in known_unreachable
    )
    assert not orphans, (
        "these modules are unreachable from every production entry point, are "
        "not declared entry points, and are not documented as unwired: "
        f"{orphans}. Either wire them (import them from a shipped entry point) "
        "or declare them here with the reason they are not shipped."
    )


def test_the_analyzer_is_not_a_stub() -> None:
    """Guard against an analyzer that silently matches nothing.

    If ``_CALL_SITES`` were empty for every symbol, every allow-list entry would
    trivially satisfy ``test_allow_listed_capability_is_genuinely_unwired`` and
    the whole gate would be vacuous. This pins the expected ratio of wired to
    unwired public capabilities.
    """
    capabilities = _all_public_capabilities()
    wired = [k for k in capabilities if _wired(*k)]
    assert len(wired) > 50, (
        f"only {len(wired)} of {len(capabilities)} public capabilities read as "
        "wired; the analyzer is almost certainly broken"
    )
    assert len(wired) < len(capabilities), (
        "every public capability reads as wired, which means the analyzer is not discriminating"
    )


def test_duplicate_public_names_across_modules_are_not_confused() -> None:
    """Same-named symbols in different modules must be tracked separately.

    The tree has two distinct ``redact_value`` functions (``validsim.logging``
    and ``validsim.project_config``) and two distinct ``discover_config_file``
    functions (``validsim.config_loader`` and ``validsim.project_config``). A
    name-keyed analyzer silently conflates them, which would let one module's
    wiring vouch for another's dead code.
    """
    by_name: dict[str, list[str]] = {}
    for module, symbol in _SYMBOL_HOME:
        by_name.setdefault(symbol, []).append(module)
    collisions = {s: ms for s, ms in by_name.items() if len(ms) > 1}
    assert collisions, (
        "expected at least one same-named public symbol pair in the tree "
        f"(the analyzer's per-module keying is untested); found {collisions}"
    )
    for symbol, modules in collisions.items():
        for module in modules:
            assert (module, symbol) in _SYMBOL_HOME
