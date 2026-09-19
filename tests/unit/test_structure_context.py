"""vo.observation.structure_context -- the same regime read at several
timeframes, and the nesting between them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.regime import RegimeDirection, RegimeType
from vo.observation.regime_config import load_regime_config
from vo.observation.structure_context import (
    MultiTimeframeStructure,
    StructuralAlignment,
    StructureContext,
    StructureContextError,
    TimeframeStructure,
)
from vo.observation.swing_config import load_swing_config

_REPO = Path(__file__).resolve().parents[2]
_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_AT = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


def _bar(minute: int, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_START + timedelta(minutes=minute),
        open=close,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _engine(higher: list[Timeframe]) -> MultiTimeframeStructure:
    return MultiTimeframeStructure(
        regime_config=load_regime_config(_REPO / "config" / "settings" / "regime.yaml"),
        swing_config=load_swing_config(_REPO / "config" / "settings" / "swings.yaml"),
        tick_size=0.01,
        higher_timeframes=higher,
    )


def _structure(
    timeframe: Timeframe,
    regime: RegimeType | None = None,
    direction: RegimeDirection | None = None,
) -> TimeframeStructure:
    return TimeframeStructure(
        timeframe=timeframe,
        regime=regime,
        direction=direction,
        observed_at=_AT if regime else None,
        bars_completed=10,
        state_object_id="rs:1" if regime else None,
    )


# ── alignment, the one derived fact ───────────────────────────────────────


def test_matching_directions_are_aligned() -> None:
    context = StructureContext(
        generated_at_utc=_AT,
        execution=_structure(Timeframe.M1, RegimeType.EXPANSION, RegimeDirection.UP),
        higher=(_structure(Timeframe.H1, RegimeType.EXPANSION, RegimeDirection.UP),),
    )

    assert context.alignment_with(Timeframe.H1) is StructuralAlignment.ALIGNED


def test_opposing_directions_are_counter() -> None:
    context = StructureContext(
        generated_at_utc=_AT,
        execution=_structure(Timeframe.M1, RegimeType.EXPANSION, RegimeDirection.UP),
        higher=(_structure(Timeframe.H1, RegimeType.EXPANSION, RegimeDirection.DOWN),),
    )

    assert context.alignment_with(Timeframe.H1) is StructuralAlignment.COUNTER


def test_a_directionless_regime_gives_undefined_rather_than_assumed_agreement() -> None:
    """A consolidation has no direction to agree with. Guessing agreement
    from absence is how a filter quietly becomes wrong."""
    context = StructureContext(
        generated_at_utc=_AT,
        execution=_structure(Timeframe.M1, RegimeType.EXPANSION, RegimeDirection.UP),
        higher=(_structure(Timeframe.H1, RegimeType.CONSOLIDATION, None),),
    )

    assert context.alignment_with(Timeframe.H1) is StructuralAlignment.UNDEFINED


def test_an_unseen_timeframe_is_undefined() -> None:
    context = StructureContext(
        generated_at_utc=_AT,
        execution=_structure(Timeframe.M1, RegimeType.EXPANSION, RegimeDirection.UP),
        higher=(),
    )

    assert context.alignment_with(Timeframe.H4) is StructuralAlignment.UNDEFINED
    assert context.at(Timeframe.H4) is None


def test_the_description_names_the_nesting() -> None:
    """The exact sentence the user described wanting to be able to see."""
    context = StructureContext(
        generated_at_utc=_AT,
        execution=_structure(
            Timeframe.M1, RegimeType.PULLBACK_UNRESOLVED, None
        ),
        higher=(_structure(Timeframe.H1, RegimeType.EXPANSION, RegimeDirection.UP),),
    )

    assert context.describe() == "M1 PULLBACK_UNRESOLVED inside H1 EXPANSION UP"


def test_an_unknown_higher_timeframe_is_left_out_of_the_description() -> None:
    context = StructureContext(
        generated_at_utc=_AT,
        execution=_structure(Timeframe.M1, RegimeType.EXPANSION, RegimeDirection.UP),
        higher=(_structure(Timeframe.D1),),
    )

    assert context.describe() == "M1 EXPANSION UP"


# ── wiring and the no-lookahead consequence ───────────────────────────────


def test_a_higher_timeframe_stays_unknown_until_one_of_its_bars_closes() -> None:
    """The honest consequence of refusing partial bars: for the first 14
    M1 bars, M15 has completed nothing and says so."""
    structure = _engine([Timeframe.M15])
    for minute in range(14):
        structure.push(_bar(minute, 20_000.0 + minute))

    snapshot = structure.snapshot(generated_at_utc=_AT)
    m15 = snapshot.at(Timeframe.M15)

    assert m15 is not None
    assert m15.bars_completed == 0
    assert m15.known is False


def test_a_higher_timeframe_advances_once_its_bars_complete() -> None:
    structure = _engine([Timeframe.M15])
    for minute in range(90):
        structure.push(_bar(minute, 20_000.0 + minute))

    m15 = structure.snapshot(generated_at_utc=_AT).at(Timeframe.M15)

    assert m15 is not None
    assert m15.bars_completed >= 4, "90 M1 bars should complete several M15 buckets"


def test_the_execution_timeframe_counts_every_bar() -> None:
    structure = _engine([Timeframe.M15])
    for minute in range(30):
        structure.push(_bar(minute, 20_000.0 + minute))

    assert structure.snapshot(generated_at_utc=_AT).execution.bars_completed == 30


def test_several_higher_timeframes_advance_at_their_own_rates() -> None:
    structure = _engine([Timeframe.M5, Timeframe.M15, Timeframe.H1])
    for minute in range(120):
        structure.push(_bar(minute, 20_000.0 + minute))

    snapshot = structure.snapshot(generated_at_utc=_AT)
    m5 = snapshot.at(Timeframe.M5)
    m15 = snapshot.at(Timeframe.M15)
    h1 = snapshot.at(Timeframe.H1)

    assert m5 is not None and m15 is not None and h1 is not None
    assert m5.bars_completed > m15.bars_completed > h1.bars_completed


def test_a_state_carries_its_object_id_for_traceability() -> None:
    """G8: a display built on this must trace back to a real record."""
    structure = _engine([Timeframe.M5])
    for minute in range(200):
        structure.push(_bar(minute, 20_000.0 + minute * 0.5))

    execution = structure.snapshot(generated_at_utc=_AT).execution

    if execution.known:
        assert execution.state_object_id


# ── setup validation ──────────────────────────────────────────────────────


def test_an_unsupported_higher_timeframe_is_refused() -> None:
    with pytest.raises(StructureContextError, match="unsupported higher timeframes"):
        _engine([Timeframe.M1])


def test_the_execution_timeframe_cannot_also_be_a_higher_one() -> None:
    with pytest.raises(StructureContextError, match="both the execution timeframe"):
        MultiTimeframeStructure(
            regime_config=load_regime_config(_REPO / "config" / "settings" / "regime.yaml"),
            swing_config=load_swing_config(_REPO / "config" / "settings" / "swings.yaml"),
            tick_size=0.01,
            higher_timeframes=[Timeframe.M15],
            execution_timeframe=Timeframe.M15,
        )
