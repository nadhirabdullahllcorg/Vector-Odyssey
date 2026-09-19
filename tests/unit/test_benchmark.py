"""vo.telemetry.benchmark -- progress against the account's high-water
mark, and the guarantee that it stays reporting rather than targeting."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vo.market.account import AccountState
from vo.telemetry.benchmark import (
    BenchmarkProgress,
    measure_benchmark_progress,
    render_progress_line,
)

_AT = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


def _account(equity: float) -> AccountState:
    return AccountState(
        login=5150234,
        name="J. Nazir",
        server="1xTrade-Server",
        currency="USD",
        balance=equity,
        equity=equity,
        profit=0.0,
        margin=0.0,
        margin_free=equity,
        margin_level=None,
        leverage=100,
        trade_allowed=True,
    )


def test_it_measures_the_gap_back_to_the_peak() -> None:
    progress = measure_benchmark_progress(
        _account(100_000.0), high_water_mark=127_000.0, observed_at_utc=_AT
    )

    assert progress.distance_to_high_water_mark == 27_000.0
    assert progress.recovered is False
    assert progress.fraction_of_high_water_mark == pytest.approx(0.7874, abs=1e-4)


def test_being_above_the_peak_is_not_a_negative_shortfall() -> None:
    progress = measure_benchmark_progress(
        _account(130_000.0), high_water_mark=127_000.0, observed_at_utc=_AT
    )

    assert progress.distance_to_high_water_mark == 0.0
    assert progress.recovered is True


def test_a_nonsense_mark_is_refused() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        measure_benchmark_progress(_account(100.0), high_water_mark=0.0, observed_at_utc=_AT)


def test_the_rendered_line_states_the_gap_and_stops() -> None:
    """No encouragement, no projection, no implied plan -- a reader
    deserves the number, not a nudge."""
    line = render_progress_line(
        BenchmarkProgress(
            observed_at_utc=_AT, high_water_mark=127_000.0, current_equity=100_000.0
        )
    )

    assert "27000.00 below the high-water mark" in line
    for nudge in ("need", "target", "should", "recover by", "to go"):
        assert nudge not in line.lower()


def test_the_type_exposes_no_quantity_that_could_be_multiplied_into_a_size() -> None:
    """The absence of a 'required return' or 'suggested size' field is
    deliberate. If one is ever added, this test should fail and the
    addition should be argued for explicitly."""
    exposed = {
        name
        for name in dir(BenchmarkProgress)
        if not name.startswith("_")
    }

    assert exposed == {
        "observed_at_utc",
        "high_water_mark",
        "current_equity",
        "distance_to_high_water_mark",
        "fraction_of_high_water_mark",
        "recovered",
    }
