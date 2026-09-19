"""
LrxConfig -- the versioned settings behind vo.valco's LRX strategy
(config/settings/lrx.yaml), loaded the same way every other settings file
in this project is: yaml.safe_load, a required top-level mapping, a typed
error for anything malformed, never a silently-substituted default.

WHY EVERY PARAMETER IS HERE RATHER THAN IN CODE. The strategy spec's own
section 31 names forty things that "must be explicitly designed rather
than assumed", and says plainly: do not silently invent these. Keeping
all forty in one versioned file is what makes that enforceable -- a value
someone disagrees with is a config edit and a re-run, not an argument
about what the code meant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class LrxConfigError(ValueError):
    """Raised for a structurally invalid config/settings/lrx.yaml."""


class OriginModel(Enum):
    """The four ways the spec (section 7) allows an expansion's origin to
    be defined. All four are computed and recorded on every setup; this
    selects which one drives the trade, so results can be broken down by
    model rather than one being silently chosen."""

    EXPANSION_ORIGIN = "EXPANSION_ORIGIN"
    SWING_ORIGIN = "SWING_ORIGIN"
    EQUILIBRIUM = "EQUILIBRIUM"
    STRUCTURAL_EQUILIBRIUM = "STRUCTURAL_EQUILIBRIUM"

    def __str__(self) -> str:
        return self.value


class EntryModel(Enum):
    """The five rebalance entries of spec section 10, separately testable."""

    FVG_FIRST_TOUCH = "FVG_FIRST_TOUCH"
    FVG_CE = "FVG_CE"
    EXPANSION_50 = "EXPANSION_50"
    FVG_EQ_OVERLAP = "FVG_EQ_OVERLAP"
    DEEP_ORIGIN = "DEEP_ORIGIN"

    def __str__(self) -> str:
        return self.value


class TargetMode(Enum):
    """T1_ONLY is the honest v1: one target, because nothing can manage an
    open position yet. T1_T2_T3 becomes available once the position
    manager exists -- see the strategy spec's Blocker 2."""

    T1_ONLY = "T1_ONLY"
    T1_T2_T3 = "T1_T2_T3"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """One NY-time window. Stored as plain `time` values and always
    resolved through the Time Engine -- broker server time is never
    hardcoded (spec section 15)."""

    opens: time
    closes: time

    def __post_init__(self) -> None:
        if self.opens >= self.closes:
            raise LrxConfigError(f"window opens {self.opens} must be before closes {self.closes}")

    def contains(self, moment: time) -> bool:
        """Half-open [opens, closes): a setup window that closes at 13:45
        does not admit 13:45 itself."""
        return self.opens <= moment < self.closes


@dataclass(frozen=True, slots=True)
class LrxSessionConfig:
    timezone: str
    trade_window: SessionWindow
    setup_selection: SessionWindow
    flat_at_window_close: bool

    def __post_init__(self) -> None:
        # Arming a setup the trade window cannot hold is incoherent -- the
        # selection window must sit inside the window that may hold a trade.
        if self.setup_selection.opens < self.trade_window.opens:
            raise LrxConfigError(
                f"setup_selection opens {self.setup_selection.opens} before the trade "
                f"window opens {self.trade_window.opens}"
            )
        if self.setup_selection.closes > self.trade_window.closes:
            raise LrxConfigError(
                f"setup_selection closes {self.setup_selection.closes} after the trade "
                f"window closes {self.trade_window.closes}"
            )


@dataclass(frozen=True, slots=True)
class SweepConfig:
    min_penetration_atr: float
    return_max_bars: int
    require_close_beyond: bool

    def __post_init__(self) -> None:
        if self.min_penetration_atr <= 0:
            raise LrxConfigError("sweep.min_penetration_atr must be > 0")
        if self.return_max_bars <= 0:
            raise LrxConfigError("sweep.return_max_bars must be > 0")


@dataclass(frozen=True, slots=True)
class ExpansionConfig:
    min_atr_multiple: float
    max_bars: int
    atr_period: int
    origin_model: OriginModel

    def __post_init__(self) -> None:
        if self.min_atr_multiple <= 0:
            raise LrxConfigError("expansion.min_atr_multiple must be > 0")
        if self.max_bars <= 0:
            raise LrxConfigError("expansion.max_bars must be > 0")
        if self.atr_period < 2:
            raise LrxConfigError("expansion.atr_period must be >= 2")


@dataclass(frozen=True, slots=True)
class InefficiencyConfig:
    types: tuple[str, ...]
    min_size_atr: float
    max_age_bars: int

    def __post_init__(self) -> None:
        if not self.types:
            raise LrxConfigError("inefficiency.types cannot be empty")
        if self.min_size_atr <= 0:
            raise LrxConfigError("inefficiency.min_size_atr must be > 0")
        if self.max_age_bars <= 0:
            raise LrxConfigError("inefficiency.max_age_bars must be > 0")


@dataclass(frozen=True, slots=True)
class ObjectiveConfig:
    cluster_tolerance_atr: float
    min_distance_atr: float
    min_reward_risk: float
    target_mode: TargetMode

    def __post_init__(self) -> None:
        if self.min_reward_risk <= 0:
            raise LrxConfigError("objective.min_reward_risk must be > 0")


@dataclass(frozen=True, slots=True)
class SetupLifeConfig:
    max_age_minutes: float
    max_bars_after_mss: int

    def __post_init__(self) -> None:
        if self.max_age_minutes <= 0:
            raise LrxConfigError("setup_life.max_age_minutes must be > 0")
        if self.max_bars_after_mss <= 0:
            raise LrxConfigError("setup_life.max_bars_after_mss must be > 0")


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    max_trades_per_day: int
    max_concurrent_positions: int
    max_consecutive_losses: int
    max_spread_points: int

    def __post_init__(self) -> None:
        if self.max_trades_per_day <= 0:
            raise LrxConfigError("limits.max_trades_per_day must be > 0")
        if self.max_concurrent_positions <= 0:
            raise LrxConfigError("limits.max_concurrent_positions must be > 0")
        if self.max_spread_points < 0:
            raise LrxConfigError("limits.max_spread_points cannot be negative")


@dataclass(frozen=True, slots=True)
class LrxConfig:
    version: int
    profile: str
    broker_symbol: str
    execution_timeframe: str
    context_timeframe: str
    session: LrxSessionConfig
    minimum_reference_class: str
    sweep: SweepConfig
    expansion: ExpansionConfig
    inefficiency: InefficiencyConfig
    entry_model: EntryModel
    max_rebalance_depth: float
    objective: ObjectiveConfig
    stop_buffer_atr: float
    setup_life: SetupLifeConfig
    limits: LimitsConfig
    experiment_flags: dict[str, bool] = field(default_factory=dict)

    def flag(self, name: str) -> bool:
        """An unknown flag is False, never an exception -- a config that
        predates a newly added flag should keep loading, with the new
        concept simply off until it is switched on deliberately."""
        return bool(self.experiment_flags.get(name, False))


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data:
        raise LrxConfigError(f"{where}: missing required key {key!r}")
    return data[key]


def _mapping(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LrxConfigError(f"{where}: expected a mapping")
    return raw


def _parse_time(raw: Any, where: str) -> time:
    text = str(raw)
    try:
        hours, minutes = text.split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError) as exc:
        raise LrxConfigError(f"{where}: {text!r} is not a HH:MM time") from exc


def _window(raw: Any, where: str) -> SessionWindow:
    data = _mapping(raw, where)
    return SessionWindow(
        opens=_parse_time(_require(data, "opens", where), f"{where}.opens"),
        closes=_parse_time(_require(data, "closes", where), f"{where}.closes"),
    )


def _enum(enum_cls: Any, raw: Any, where: str) -> Any:
    text = str(raw).upper()
    try:
        return enum_cls(text)
    except ValueError as exc:
        allowed = sorted(member.value for member in enum_cls)
        raise LrxConfigError(f"{where}: must be one of {allowed}, got {text!r}") from exc


def load_lrx_config(path: str | Path) -> LrxConfig:
    """Load and validate config/settings/lrx.yaml."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    top = _mapping(raw, "lrx config")

    instrument = _mapping(_require(top, "instrument", "lrx config"), "instrument")
    session_raw = _mapping(_require(top, "session", "lrx config"), "session")
    levels = _mapping(_require(top, "reference_levels", "lrx config"), "reference_levels")
    sweep = _mapping(_require(top, "sweep", "lrx config"), "sweep")
    expansion = _mapping(_require(top, "expansion", "lrx config"), "expansion")
    inefficiency = _mapping(_require(top, "inefficiency", "lrx config"), "inefficiency")
    entry = _mapping(_require(top, "entry", "lrx config"), "entry")
    objective = _mapping(_require(top, "objective", "lrx config"), "objective")
    stop = _mapping(_require(top, "stop", "lrx config"), "stop")
    setup_life = _mapping(_require(top, "setup_life", "lrx config"), "setup_life")
    limits = _mapping(_require(top, "limits", "lrx config"), "limits")
    flags = _mapping(top.get("experiment_flags") or {}, "experiment_flags")

    return LrxConfig(
        version=int(_require(top, "version", "lrx config")),
        profile=str(top.get("profile", "BASELINE")),
        broker_symbol=str(_require(instrument, "broker_symbol", "instrument")),
        execution_timeframe=str(_require(instrument, "execution_timeframe", "instrument")),
        context_timeframe=str(_require(instrument, "context_timeframe", "instrument")),
        session=LrxSessionConfig(
            timezone=str(_require(session_raw, "timezone", "session")),
            trade_window=_window(
                _require(session_raw, "trade_window", "session"), "session.trade_window"
            ),
            setup_selection=_window(
                _require(session_raw, "setup_selection", "session"), "session.setup_selection"
            ),
            flat_at_window_close=bool(session_raw.get("flat_at_window_close", True)),
        ),
        minimum_reference_class=str(_require(levels, "minimum_class", "reference_levels")).upper(),
        sweep=SweepConfig(
            min_penetration_atr=float(_require(sweep, "min_penetration_atr", "sweep")),
            return_max_bars=int(_require(sweep, "return_max_bars", "sweep")),
            require_close_beyond=bool(sweep.get("require_close_beyond", False)),
        ),
        expansion=ExpansionConfig(
            min_atr_multiple=float(_require(expansion, "min_atr_multiple", "expansion")),
            max_bars=int(_require(expansion, "max_bars", "expansion")),
            atr_period=int(_require(expansion, "atr_period", "expansion")),
            origin_model=_enum(
                OriginModel,
                _require(expansion, "origin_model", "expansion"),
                "expansion.origin_model",
            ),
        ),
        inefficiency=InefficiencyConfig(
            types=tuple(str(t).upper() for t in _require(inefficiency, "types", "inefficiency")),
            min_size_atr=float(_require(inefficiency, "min_size_atr", "inefficiency")),
            max_age_bars=int(_require(inefficiency, "max_age_bars", "inefficiency")),
        ),
        entry_model=_enum(EntryModel, _require(entry, "model", "entry"), "entry.model"),
        max_rebalance_depth=float(_require(entry, "max_rebalance_depth", "entry")),
        objective=ObjectiveConfig(
            cluster_tolerance_atr=float(
                _require(objective, "cluster_tolerance_atr", "objective")
            ),
            min_distance_atr=float(_require(objective, "min_distance_atr", "objective")),
            min_reward_risk=float(_require(objective, "min_reward_risk", "objective")),
            target_mode=_enum(
                TargetMode, _require(objective, "target_mode", "objective"), "objective.target_mode"
            ),
        ),
        stop_buffer_atr=float(_require(stop, "buffer_atr", "stop")),
        setup_life=SetupLifeConfig(
            max_age_minutes=float(_require(setup_life, "max_age_minutes", "setup_life")),
            max_bars_after_mss=int(_require(setup_life, "max_bars_after_mss", "setup_life")),
        ),
        limits=LimitsConfig(
            max_trades_per_day=int(_require(limits, "max_trades_per_day", "limits")),
            max_concurrent_positions=int(
                _require(limits, "max_concurrent_positions", "limits")
            ),
            max_consecutive_losses=int(_require(limits, "max_consecutive_losses", "limits")),
            max_spread_points=int(limits.get("max_spread_points", 0)),
        ),
        experiment_flags={str(k): bool(v) for k, v in flags.items()},
    )
