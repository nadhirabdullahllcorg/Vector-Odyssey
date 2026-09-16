"""
The canonical Bar — Phase 5's redesign (architecture/vo-candle-layer.md §2).

Phase 4 added `instrument_id` as an optional, backward-compatible field.
Phase 5 is where the candle-layer doc says the fuller shape belongs, so this
version finishes that: `instrument_id` and `timeframe` are now required and
typed (InstrumentId, Timeframe — no more raw strings standing in for
identity, closing C4 alongside Phase 4's E.5), `timestamp` is renamed to
`open_time_utc` to match the spec's naming, and tick-provenance fields
(`spread`, `source_feed`, `source_tick_start/end`, `captured_tick_count`)
exist so the candle layer and future coverage checks (C7) have somewhere to
read from.

One field the spec describes is deliberately NOT here, permanently, not
just for now:

  `temporal: Optional[TimeContext]` — `vo.time.TimeContext` (Phase 6) is a
  layer-2 type, and `vo.market` (layer 1) may not import `vo.time` (see
  tests/unit/test_architecture.py's layering test) even for an Optional
  field on a dataclass. Phase 6 attaches temporal context by composition
  instead (see vo.time.context.TemporalBar) rather than by extending Bar —
  the same "wrap it in a view, don't mutate the record" pattern Candle
  already established for geometry in Phase 5.

`provenance: Provenance | None = None` (below) is Phase 6's doing: it is a
plain vo.market value type (see provenance.py) with no dependency on
vo.time, so Bar can hold it without an upward import. vo.time populates it
once it has resolved a real UTC offset; until then (schema v1, or an
unresolved v2 record) it stays None.

`source_tick_start`/`source_tick_end`/`captured_tick_count` are always None
today for the same reason: no wire record — v1 or v2 — carries a per-bar
tick range yet (BarRecordV2 has a single `seq`, not a range). They exist on
Bar now so the shape is right and TickCoverage is usable the moment a
future bridge iteration starts populating them; until then, None is the
honest answer (C7: None means UNKNOWN, never a guessed zero).

`bar_id` is a *computed* property, not a stored field, even though the spec
lists it under "identity" as a stored value. Storing a value that is
entirely deterministic from other fields on the same object is exactly what
C2 warns against for Candle's derived geometry — the same reasoning applies
here. As a property it can never drift from its inputs and can never leak
into a wire format that iterates dataclass fields (nothing does, for Bar,
but the guarantee is free either way).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

from .identity import InstrumentId
from .provenance import Provenance
from .timeframe import Timeframe


class TickFeed(Enum):
    """How a bar's constituent ticks (if any) were obtained. See C6/C7."""

    NONE = auto()
    """OHLC only — no tick data exists for this bar. The default: nothing
    on the wire today claims otherwise."""
    LIVE_ONTICK = auto()
    """Accumulated in the EA's OnTick — may drop ticks under load."""
    COPY_TICKS_RANGE = auto()
    """Pulled from the terminal's stored tick history — exact, verifiable."""
    REPLAY = auto()
    """From a captured VO archive. Not producible from today's wire; set
    directly by the replay harness once it exists (Phase 9)."""
    BACKTEST_SYNTH = auto()
    """Synthesized by the strategy tester — never valid for tick research.
    Not producible from today's wire; set directly by backtest fill
    simulation once it exists (Phase 25)."""

    @classmethod
    def from_wire(cls, value: str | None) -> "TickFeed":
        """
        BarRecordV2.source_feed is documented as "live" (OnTick) or
        "history" (CopyTicksRange/CopyRates backfill) — the only two
        strings any current bridge emits. None (v1 records, which have no
        source_feed field at all) maps to NONE, not a guess.
        """
        if value is None:
            return cls.NONE

        mapping = {"live": cls.LIVE_ONTICK, "history": cls.COPY_TICKS_RANGE}

        if value not in mapping:
            raise ValueError(f"Unrecognized source_feed: {value!r}")

        return mapping[value]


class DataQuality(Enum):
    """
    Bar-level quality state. A bar that fails its own geometry invariants
    (__post_init__ below) is never constructed at all — this is a
    different, later kind of judgement: BarSequence.append quarantines a
    bar that is individually well-formed but bad *in sequence* (duplicate,
    out of order). See vo.market.sequence.
    """

    VALID = auto()
    SUSPECT = auto()
    QUARANTINED = auto()


@dataclass(frozen=True)
class Bar:
    """
    Canonical representation of a single market bar.

    This object represents market data only.
    It contains no trading interpretation or strategy logic.
    """

    instrument_id: InstrumentId
    timeframe: Timeframe
    open_time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    real_volume: int
    spread: int | None = None
    source_feed: TickFeed = TickFeed.NONE
    source_tick_start: int | None = None
    source_tick_end: int | None = None
    captured_tick_count: int | None = None
    quality: DataQuality = DataQuality.VALID
    quality_reason: str | None = None
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, InstrumentId):
            raise TypeError("Bar instrument_id must be an InstrumentId, not a raw string")
        if not isinstance(self.timeframe, Timeframe):
            raise TypeError("Bar timeframe must be a Timeframe, not a raw string")
        if self.open_time_utc.tzinfo is None:
            raise ValueError("Bar open_time_utc must be timezone-aware")
        if self.tick_volume < 0:
            raise ValueError("Bar tick_volume cannot be negative")
        if self.high < self.low:
            raise ValueError("Bar high cannot be below bar low")
        if self.open > self.high:
            raise ValueError("Bar open cannot be above bar high")
        if self.open < self.low:
            raise ValueError("Bar open cannot be below bar low")
        if self.close > self.high:
            raise ValueError("Bar close cannot be above bar high")
        if self.close < self.low:
            raise ValueError("Bar close cannot be below bar low")
        if self.spread is not None and self.spread < 0:
            raise ValueError("Bar spread cannot be negative")
        if self.captured_tick_count is not None and self.captured_tick_count < 0:
            raise ValueError("Bar captured_tick_count cannot be negative")

    @property
    def bar_id(self) -> str:
        """
        Deterministic, reproducible across live, replay and backtest for
        the same observation — see the module docstring for why this is a
        property rather than a stored field.
        """
        return (
            f"{self.instrument_id.key}:{self.timeframe.canonical}:"
            f"{self.open_time_utc.isoformat()}"
        )
