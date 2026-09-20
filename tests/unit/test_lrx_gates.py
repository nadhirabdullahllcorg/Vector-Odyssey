"""vo.valco.lrx_gates -- may LRX arm a setup right now?"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from vo.valco.lrx_config import load_lrx_config
from vo.valco.lrx_gates import ArmingBlock, ArmingDecision, evaluate_arming, must_flatten

_REPO = Path(__file__).resolve().parents[2]
_CONFIG = load_lrx_config(_REPO / "config" / "settings" / "lrx.yaml")

_IN_WINDOW = datetime(2026, 9, 21, 10, 30)      # inside 09:00-13:45
_EARLY = datetime(2026, 9, 21, 7, 30)           # trade window open, selection not
_CLOSED = datetime(2026, 9, 21, 15, 0)          # outside everything


def _arming(**overrides: object) -> ArmingDecision:
    base: dict[str, object] = dict(
        now_ny=_IN_WINDOW,
        preflight_passed=True,
        compliance_allows=True,
        open_position_count=0,
        trades_today=0,
        consecutive_losses=0,
    )
    base.update(overrides)
    return evaluate_arming(_CONFIG, **base)  # type: ignore[arg-type]


def test_a_clean_state_inside_the_window_may_arm() -> None:
    decision = _arming()

    assert decision.allowed is True
    assert decision.blocks == ()
    assert decision.reason is None


def test_an_unproven_execution_path_blocks_everything() -> None:
    """The whole point of preflight: nothing arms until the plumbing has
    moved a real order."""
    decision = _arming(preflight_passed=False)

    assert decision.allowed is False
    assert ArmingBlock.PREFLIGHT_NOT_PASSED in decision.blocks


def test_before_setup_selection_opens_the_trade_window_alone_is_not_enough() -> None:
    """07:30 is inside 07:00-14:00 but before 09:00 -- positions may be
    managed, nothing new may be armed."""
    decision = _arming(now_ny=_EARLY)

    assert decision.allowed is False
    assert ArmingBlock.OUTSIDE_SETUP_WINDOW in decision.blocks
    assert ArmingBlock.OUTSIDE_TRADE_WINDOW not in decision.blocks


def test_outside_the_trade_window_only_the_broader_block_is_reported() -> None:
    """Listing both windows when the outer one is shut is noise, not
    detail."""
    decision = _arming(now_ny=_CLOSED)

    assert ArmingBlock.OUTSIDE_TRADE_WINDOW in decision.blocks
    assert ArmingBlock.OUTSIDE_SETUP_WINDOW not in decision.blocks


def test_compliance_blocking_is_reported_as_one_fact() -> None:
    decision = _arming(compliance_allows=False)

    assert ArmingBlock.COMPLIANCE_BLOCKED in decision.blocks


def test_one_position_at_a_time() -> None:
    decision = _arming(open_position_count=1)

    assert ArmingBlock.POSITION_ALREADY_OPEN in decision.blocks


def test_the_daily_trade_cap_is_one() -> None:
    """Baseline locked 2026-09-20. The gate reads the shipped config, so
    this test moves with lrx.yaml by design -- it asserts the cap BINDS
    at the configured number, and that the first trade of the day is
    still allowed through it."""
    assert _arming(trades_today=0).allowed is True
    assert ArmingBlock.DAILY_TRADE_CAP_REACHED in _arming(trades_today=1).blocks


def test_consecutive_losses_stop_the_day() -> None:
    assert ArmingBlock.CONSECUTIVE_LOSS_CAP_REACHED in _arming(consecutive_losses=2).blocks


def test_every_blocking_reason_is_reported_not_just_the_first() -> None:
    """One reason is a worse answer than three when the question is 'why
    did nothing happen today'."""
    decision = _arming(
        preflight_passed=False,
        compliance_allows=False,
        open_position_count=1,
        trades_today=9,
        consecutive_losses=5,
        now_ny=_CLOSED,
    )

    assert len(decision.blocks) >= 5
    assert decision.reason is not None
    assert "PREFLIGHT_NOT_PASSED" in decision.reason
    assert "COMPLIANCE_BLOCKED" in decision.reason


def test_a_wide_spread_blocks_arming() -> None:
    assert ArmingBlock.SPREAD_TOO_WIDE in _arming(spread_points=250).blocks
    assert _arming(spread_points=80).allowed is True


def test_an_unknown_spread_skips_the_check_rather_than_inventing_one() -> None:
    """A backtest that does not model spread should not be silently
    treated as having a spread of zero."""
    assert _arming(spread_points=None).allowed is True


def test_a_decision_cannot_be_blocked_without_a_reason() -> None:
    with pytest.raises(ValueError, match="must say why"):
        ArmingDecision(allowed=False, blocks=())


def test_an_allowed_decision_cannot_carry_blocks() -> None:
    with pytest.raises(ValueError, match="cannot carry blocking reasons"):
        ArmingDecision(allowed=True, blocks=(ArmingBlock.COMPLIANCE_BLOCKED,))


# ── flattening is governed by the trade window, not the setup window ──────


def test_a_position_may_run_past_the_setup_window() -> None:
    """A trade entered at 13:44 keeps running until 14:00."""
    assert must_flatten(_CONFIG, now_ny=datetime(2026, 9, 21, 13, 50)) is False


def test_a_position_must_be_flat_after_the_trade_window() -> None:
    assert must_flatten(_CONFIG, now_ny=_CLOSED) is True


def test_flattening_is_skipped_when_the_config_says_so() -> None:
    import dataclasses

    session = dataclasses.replace(_CONFIG.session, flat_at_window_close=False)
    config = dataclasses.replace(_CONFIG, session=session)

    assert must_flatten(config, now_ny=_CLOSED) is False
