"""
Architectural boundaries, enforced mechanically.

These tests exist because architectural rules that live only in a document stop
being true within a month. Each one fails the build rather than filing a
complaint.

    G1  every declared concept carries a provenance tag; [ICT] carries a source
    G2  no [VO-H] concept is reachable from the trade decision path
    G7  the EA contains no strategy logic; nothing but the broker adapter
        imports MetaTrader5
    G14 risk is the sole producer of TradeSignal

Plus the layering rule: a module may import its own layer or lower, never higher.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from vo.interfaces import REGISTRY, ConceptTag

REPO_ROOT = Path(__file__).resolve().parents[2]
VO_ROOT = REPO_ROOT / "python" / "vo"


# ── layering ───────────────────────────────────────────────────────────────
#
# Lower numbers may not import higher ones. `vo.interfaces` is layer 0 and
# therefore depends on nothing in vo at all.

LAYERS: dict[str, int] = {
    "vo.interfaces": 0,
    "vo.market": 1,
    "vo.time": 2,
    "vo.observation": 3,
    "vo.month01": 4,
    "vo.research": 5,
    "vo.valco": 6,
    "vo.signals": 7,
    "vo.allocation": 8,
    "vo.risk": 9,
    "vo.compliance": 10,
    "vo.core": 11,
    "vo.execution": 12,
    "vo.telemetry": 13,
}

# Only this module may talk to the terminal.
MT5_ADAPTER_MODULES = frozenset({"vo.market.mt5", "vo.core.mt5"})


def _python_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and not p.name.startswith(".")
    ]


def _module_name(path: Path, package_root: Path) -> str:
    rel = path.relative_to(package_root.parent).with_suffix("")
    parts = list(rel.parts)

    if parts[-1] == "__init__":
        parts.pop()

    return ".".join(parts)


def _imports_of(path: Path) -> set[str]:
    """Every module this file imports, as dotted names."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)

            # `from pkg import thing` may be importing a SUBMODULE, not a
            # name. Record both spellings; unresolvable ones are filtered
            # out later against the set of real modules.
            for alias in node.names:
                found.add(f"{node.module}.{alias.name}")

    return found


def _layer_of(module: str) -> int | None:
    for prefix, layer in LAYERS.items():
        if module == prefix or module.startswith(prefix + "."):
            return layer
    return None


def test_every_vo_subpackage_has_a_declared_layer() -> None:
    """A new subpackage must be placed in the layering before it can be used."""
    undeclared = set()

    for path in _python_files(VO_ROOT):
        module = _module_name(path, VO_ROOT)

        if module == "vo":
            continue

        if _layer_of(module) is None:
            undeclared.add(module)

    assert not undeclared, (
        f"These modules have no declared layer: {sorted(undeclared)}. "
        f"Add them to LAYERS, deciding deliberately where they sit."
    )


def test_modules_do_not_import_higher_layers() -> None:
    violations: list[str] = []

    for path in _python_files(VO_ROOT):
        module = _module_name(path, VO_ROOT)
        own_layer = _layer_of(module)

        if own_layer is None:
            continue

        for imported in _imports_of(path):
            other_layer = _layer_of(imported)

            if other_layer is None:
                continue

            if other_layer > own_layer:
                violations.append(
                    f"{module} (layer {own_layer}) imports "
                    f"{imported} (layer {other_layer})"
                )

    assert not violations, "Upward imports break the layering:\n  " + "\n  ".join(
        violations
    )


def test_interfaces_depends_on_nothing_outside_itself() -> None:
    """Layer 0 is only layer 0 if it really depends on nothing."""
    violations: list[str] = []

    interfaces_root = VO_ROOT / "interfaces"

    for path in _python_files(interfaces_root):
        module = _module_name(path, VO_ROOT)

        for imported in _imports_of(path):
            if imported.startswith("vo.") and not imported.startswith("vo.interfaces"):
                violations.append(f"{module} imports {imported}")

    assert not violations, (
        "vo.interfaces is the contract layer and must stay free of dependencies:\n  "
        + "\n  ".join(violations)
    )


def test_only_the_broker_adapter_imports_metatrader5() -> None:
    """
    Gate G7. Scattering MT5 calls through the codebase is what makes a system
    impossible to backtest honestly.
    """
    offenders: list[str] = []

    for path in _python_files(VO_ROOT):
        module = _module_name(path, VO_ROOT)

        if module in MT5_ADAPTER_MODULES:
            continue

        for imported in _imports_of(path):
            if imported == "MetaTrader5" or imported.startswith("MetaTrader5."):
                offenders.append(module)

    assert not offenders, (
        f"Only {sorted(MT5_ADAPTER_MODULES)} may import MetaTrader5. "
        f"Offenders: {sorted(offenders)}"
    )


# ── G14: risk is the sole TradeSignal producer ──────────────────────────────
#
# Phase 14's own gate (architecture/vo-phase-plan.md Block 4): "risk is the
# sole TradeSignal producer." Enforced the same way G7 enforces its MT5
# import rule: by scanning for the one thing that must not happen anywhere
# else -- here, a TradeSignal( constructor call -- rather than trusting a
# docstring to stay true.

TRADE_SIGNAL_PRODUCER_MODULES = frozenset({"vo.risk.manager"})


def test_risk_is_the_sole_trade_signal_producer() -> None:
    offenders: list[str] = []

    for path in _python_files(VO_ROOT):
        module = _module_name(path, VO_ROOT)

        if module in TRADE_SIGNAL_PRODUCER_MODULES:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)

            if name == "TradeSignal":
                offenders.append(module)

    assert not offenders, (
        f"Only {sorted(TRADE_SIGNAL_PRODUCER_MODULES)} may construct a TradeSignal. "
        f"Offenders: {sorted(set(offenders))}"
    )


# G15 (architecture/vo-phase-plan.md, the Account Compliance Engine): "no
# TradeSignal reaches execution without a ComplianceApproval." Enforced the
# identical way G14 enforces its own TradeSignal rule -- by scanning for the
# one thing that must not happen anywhere else, a ComplianceApproval(
# constructor call -- rather than trusting a docstring to stay true.

COMPLIANCE_APPROVAL_PRODUCER_MODULES = frozenset({"vo.compliance.engine"})


def test_compliance_is_the_sole_approval_producer() -> None:
    offenders: list[str] = []

    for path in _python_files(VO_ROOT):
        module = _module_name(path, VO_ROOT)

        if module in COMPLIANCE_APPROVAL_PRODUCER_MODULES:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)

            if name == "ComplianceApproval":
                offenders.append(module)

    assert not offenders, (
        f"Only {sorted(COMPLIANCE_APPROVAL_PRODUCER_MODULES)} may construct a "
        f"ComplianceApproval. Offenders: {sorted(set(offenders))}"
    )


# ── G1: concept tagging ────────────────────────────────────────────────────


def test_every_ict_concept_names_its_source() -> None:
    """A claim that ICT teaches something has to be checkable against material."""
    unsourced = [
        r.key
        for r in REGISTRY.by_tag(ConceptTag.ICT)
        if not (r.source and r.source.strip())
    ]

    assert not unsourced, f"[ICT] concepts without a source reference: {unsourced}"


def test_registry_has_no_duplicate_declarations() -> None:
    keys = [r.key for r in REGISTRY.all()]
    assert len(keys) == len(set(keys))


# ── G2: hypothesis containment ─────────────────────────────────────────────
#
# A [VO-H] concept is an untested idea. It may be measured, recorded and
# researched. It may not influence a trade until promoted with evidence.
#
# Reachability is computed over the module import graph rather than the call
# graph. That is deliberately conservative: Python's dynamic dispatch makes an
# exact call graph unreliable, and for a safety gate an over-strict answer is
# the right kind of wrong.


def _decorator_names(node: ast.AST) -> set[str]:
    names: set[str] = set()

    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec

        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)

    return names


def _declares_hypothesis(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue

        for dec in getattr(node, "decorator_list", []):
            if not isinstance(dec, ast.Call):
                continue

            func = dec.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")

            if name != "concept":
                continue

            for kw in dec.keywords:
                if kw.arg != "tag":
                    continue
                value = kw.value
                attr = value.attr if isinstance(value, ast.Attribute) else None
                if attr == "VO_H":
                    return True

    return False


def _marks_decision_path(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            "decision_path" in _decorator_names(node)
        ):
            return True

    return False


def find_hypothesis_leaks(package_root: Path, package_prefix: str) -> list[str]:
    """
    Modules on the decision path that can reach a [VO-H] declaration.

    Returns a list of human-readable violations; empty means the gate holds.
    """
    files = _python_files(package_root)

    modules: dict[str, Path] = {}
    for path in files:
        modules[_module_name(path, package_root)] = path

    hypothesis_modules = {m for m, p in modules.items() if _declares_hypothesis(p)}
    entry_points = {m for m, p in modules.items() if _marks_decision_path(p)}

    graph: dict[str, set[str]] = {}
    for module, path in modules.items():
        graph[module] = {
            imported
            for imported in _imports_of(path)
            if imported.startswith(package_prefix)
        }

    violations: list[str] = []

    for entry in sorted(entry_points):
        seen: set[str] = set()
        stack = [(entry, [entry])]

        while stack:
            current, trail = stack.pop()

            if current in seen:
                continue
            seen.add(current)

            if current in hypothesis_modules and current != entry:
                violations.append(" -> ".join(trail))
                continue

            if current == entry and current in hypothesis_modules:
                violations.append(f"{entry} (declares a hypothesis itself)")

            for nxt in sorted(graph.get(current, ())):
                if nxt in modules:
                    stack.append((nxt, [*trail, nxt]))

    return violations


def test_no_hypothesis_is_reachable_from_the_decision_path() -> None:
    leaks = find_hypothesis_leaks(VO_ROOT, "vo.")

    assert not leaks, (
        "Gate G2. A [VO-H] concept is reachable from the trade decision path. "
        "Promote it with evidence, or move it behind the research boundary:\n  "
        + "\n  ".join(leaks)
    )


def test_the_hypothesis_checker_actually_catches_a_violation(tmp_path: Path) -> None:
    """
    The gate above passes trivially while no decision path exists yet. This
    proves the checker works, so that it is already load-bearing when one does.
    """
    pkg = tmp_path / "demo"
    pkg.mkdir()

    (pkg / "__init__.py").write_text("", encoding="utf-8")

    (pkg / "guesswork.py").write_text(
        "from vo.interfaces import ConceptTag, concept\n"
        "\n"
        "@concept(tag=ConceptTag.VO_H)\n"
        "def resistance_score() -> float:\n"
        "    return 0.0\n",
        encoding="utf-8",
    )

    (pkg / "trader.py").write_text(
        "from vo.interfaces import decision_path\n"
        "from demo import guesswork\n"
        "\n"
        "@decision_path\n"
        "def decide() -> float:\n"
        "    return guesswork.resistance_score()\n",
        encoding="utf-8",
    )

    leaks = find_hypothesis_leaks(pkg, "demo")

    assert leaks, "the checker failed to notice a hypothesis on the decision path"
    assert any("guesswork" in leak for leak in leaks)


@pytest.mark.parametrize(
    "module",
    [
        "vo",
        "vo.interfaces",
        "vo.market",
        "vo.month01",
        "vo.month01.ontology",
        "vo.core",
        "vo.core.replay",
        "vo.core.config",
        "vo.core.logging_setup",
        "vo.core.pipeline",
        "vo.telemetry.publisher",
        "vo.telemetry.ea_runtime",
        "vo.telemetry.regime_feed",
        "vo.telemetry.regime_report",
        "vo.observation",
        "vo.observation.atr",
        "vo.observation.swing_config",
        "vo.observation.swings",
        "vo.observation.swing_reference",
        "vo.market.opening_range",
        "vo.market.account",
        "vo.market.mt5",
        "vo.observation.efficiency_ratio",
        "vo.observation.entry_timing",
        "vo.observation.structure_context",
        "vo.research.fbm",
        "vo.research.hurst_validation",
        "vo.research.markov_validation",
        "vo.observation.hurst",
        "vo.observation.regime",
        "vo.observation.regime_config",
        "vo.interfaces.signals",
        "vo.signals",
        "vo.signals.generator",
        "vo.allocation",
        "vo.allocation.allocator",
        "vo.risk",
        "vo.risk.risk_config",
        "vo.risk.manager",
        "vo.compliance",
        "vo.compliance.compliance_config",
        "vo.compliance.engine",
        "vo.compliance.news_gate",
        "vo.compliance.state_store",
        "vo.valco",
        "vo.valco.lrx_config",
        "vo.valco.lrx_gates",
        "vo.valco.lrx_levels",
        "vo.valco.lrx_swings",
        "vo.valco.lrx_displacement",
        "vo.valco.lrx_bos",
        "vo.valco.lrx_consolidation",
        "vo.valco.lrx_cerr",
        "vo.valco.lrx_inefficiency",
        "vo.valco.lrx_mss",
        "vo.valco.lrx_sweep",
        "vo.telemetry.benchmark",
        "vo.telemetry.live_dispatch",
        "vo.telemetry.live_wiring",
        "vo.interfaces.economic_events",
        "vo.market.economic_calendar_ingestion",
        "vo.core.mt5",
        "vo.execution",
        "vo.execution.execution_config",
        "vo.execution.types",
        "vo.execution.router",
        "vo.execution.reconciliation",
        "vo.execution.compliance_closeout",
        "vo.execution.preflight",
        "vo.telemetry.trade_pipeline",
        "vo.market.aggregate",
        "vo.observation.structure_range",
    ],
)
def test_public_packages_import_cleanly(module: str) -> None:
    __import__(module)


# ── R11: the dashboard never becomes part of the trading path ──────────────
#
# The dashboard (nominally Phase 22, scaffolded early as its own parallel
# track -- see architecture/vo-phase-plan.md SS10a) is a separate process by
# design: "EA fully functional with dashboard closed" is its own gate, and
# the architecture audit names this exact risk (R11) with this exact
# mitigation: "an architecture test forbids vo.runtime importing dashboard."
# Checked over the whole `vo` package, not just `vo.core`/`vo.runtime`,
# since any accidental import anywhere would defeat the point.


def test_vo_does_not_import_dashboard() -> None:
    offenders: list[str] = []

    for path in _python_files(VO_ROOT):
        module = _module_name(path, VO_ROOT)

        for imported in _imports_of(path):
            if imported == "dashboard" or imported.startswith("dashboard."):
                offenders.append(f"{module} imports {imported}")

    assert not offenders, (
        "Gate: the dashboard must stay a pure, optional consumer of "
        "vo.telemetry -- nothing in vo may import it back:\n  "
        + "\n  ".join(offenders)
    )



# ── G16: a recovery benchmark can never reach the sizing path ─────────────
#
# Measuring the gap back to a prior peak is legitimate reporting. Letting
# that gap influence what is traded or how big is the mechanism that turns
# a drawdown into a larger one, and the strategy spec already bans every
# mechanical form of it (no martingale, no loss-based lot escalation).
#
# vo.telemetry.benchmark sits at the top layer, so the layering test
# already makes this impossible. This test names the guarantee explicitly
# so that a future refactor moving the module somewhere "more convenient"
# fails with a reason rather than silently opening the door.

BENCHMARK_MODULE = "vo.telemetry.benchmark"
SIZING_PATH_PACKAGES = (
    "vo.valco",
    "vo.signals",
    "vo.allocation",
    "vo.risk",
    "vo.compliance",
    "vo.execution",
)


def test_benchmark_progress_never_reaches_the_sizing_path() -> None:
    """Scans real IMPORTS, not raw text: a module is free to explain in a
    comment why it must not import the benchmark (compliance_config does
    exactly that), and saying so is the opposite of an offence."""
    offenders: list[str] = []

    for path in _python_files(VO_ROOT):
        module = _module_name(path, VO_ROOT)
        if not module.startswith(SIZING_PATH_PACKAGES):
            continue
        if any(
            imported == BENCHMARK_MODULE or imported.startswith(BENCHMARK_MODULE + ".")
            for imported in _imports_of(path)
        ):
            offenders.append(module)

    assert not offenders, (
        "a recovery benchmark must never be readable from the sizing or "
        "decision path -- reporting is not targeting:\n  " + "\n  ".join(sorted(offenders))
    )
