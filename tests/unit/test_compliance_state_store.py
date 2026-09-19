"""vo.compliance.state_store -- cross-restart persistence of the
compliance engine's drawdown memory, plus the ComplianceEngine
restore/export round trip."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from vo.compliance.compliance_config import ComplianceConfig
from vo.compliance.engine import ComplianceEngine
from vo.compliance.state_store import (
    CURRENT_STATE_VERSION,
    CompliancePersistentState,
    ComplianceStateError,
    load_compliance_state,
    save_compliance_state,
)
from vo.interfaces.compliance import ComplianceStatus
from vo.market.account import AccountState

_SAVED_AT = datetime(2026, 9, 19, 18, 30, tzinfo=UTC)
_NY_OPEN = time(17, 0)


def _account(
    equity: float, *, login: int = 5150234, server: str = "1xTrade-Server"
) -> AccountState:
    return AccountState(
        login=login,
        name="J. Nazir",
        server=server,
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


def _config() -> ComplianceConfig:
    return ComplianceConfig(
        version=1,
        daily_loss_limit_fraction=0.05,
        total_drawdown_limit_fraction=0.10,
        safety_buffer_fraction=0.005,
        warning_threshold_fraction=0.70,
        critical_threshold_fraction=0.90,
    )


def _state(**overrides: object) -> CompliancePersistentState:
    base: dict[str, object] = dict(
        version=CURRENT_STATE_VERSION,
        account_login=5150234,
        account_server="1xTrade-Server",
        trading_day=date(2026, 9, 19),
        day_start_equity=10000.0,
        peak_equity=10500.0,
        total_breached=False,
        saved_at_utc=_SAVED_AT,
    )
    base.update(overrides)
    return CompliancePersistentState(**base)  # type: ignore[arg-type]


# ── the file itself ───────────────────────────────────────────────────────


def test_save_then_load_round_trips_every_field(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"
    original = _state(peak_equity=10750.25, total_breached=True)

    save_compliance_state(path, original)
    loaded = load_compliance_state(
        path, expected_login=5150234, expected_server="1xTrade-Server"
    )

    assert loaded == original


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """First run on this account -- distinct from a corrupt file."""
    loaded = load_compliance_state(
        tmp_path / "never-written.json",
        expected_login=5150234,
        expected_server="1xTrade-Server",
    )

    assert loaded is None


def test_a_corrupt_file_raises_rather_than_silently_starting_fresh(tmp_path: Path) -> None:
    """The deliberate inversion of the quarantine convention: silently
    treating this as "no state" would hand the account a fresh drawdown
    allowance."""
    path = tmp_path / "compliance_state.json"
    path.write_text("{ truncated", encoding="utf-8")

    with pytest.raises(ComplianceStateError):
        load_compliance_state(path, expected_login=5150234, expected_server="1xTrade-Server")


def test_a_wrong_account_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"
    save_compliance_state(path, _state())

    with pytest.raises(ComplianceStateError, match="refusing to restore"):
        load_compliance_state(path, expected_login=9999999, expected_server="1xTrade-Server")


def test_a_wrong_server_file_raises(tmp_path: Path) -> None:
    """Same login number on a different server is a different account --
    prop evaluation vs. live."""
    path = tmp_path / "compliance_state.json"
    save_compliance_state(path, _state())

    with pytest.raises(ComplianceStateError, match="refusing to restore"):
        load_compliance_state(path, expected_login=5150234, expected_server="OtherBroker-Live")


def test_an_unsupported_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"
    save_compliance_state(path, _state())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["version"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ComplianceStateError, match="unsupported compliance state version"):
        load_compliance_state(path, expected_login=5150234, expected_server="1xTrade-Server")


def test_a_missing_required_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"
    save_compliance_state(path, _state())
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["peak_equity"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ComplianceStateError, match="malformed"):
        load_compliance_state(path, expected_login=5150234, expected_server="1xTrade-Server")


def test_saving_over_an_existing_file_replaces_it(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"
    save_compliance_state(path, _state(peak_equity=10000.0))
    save_compliance_state(path, _state(peak_equity=11000.0))

    loaded = load_compliance_state(
        path, expected_login=5150234, expected_server="1xTrade-Server"
    )

    assert loaded is not None
    assert loaded.peak_equity == 11000.0


def test_no_temp_files_are_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"
    save_compliance_state(path, _state())

    assert sorted(p.name for p in tmp_path.iterdir()) == ["compliance_state.json"]


def test_a_day_anchor_without_its_day_is_rejected() -> None:
    with pytest.raises(ComplianceStateError, match="set or unset together"):
        _state(trading_day=None, day_start_equity=10000.0)


def test_a_naive_saved_at_is_rejected() -> None:
    with pytest.raises(ComplianceStateError, match="tz-aware"):
        _state(saved_at_utc=datetime(2026, 9, 19, 18, 30))


# ── the engine round trip ─────────────────────────────────────────────────


def test_peak_equity_survives_a_restart(tmp_path: Path) -> None:
    """The headline case: without this, an EA restarted after a drawdown
    re-anchors its peak to current equity and silently grants itself a
    fresh full drawdown allowance."""
    path = tmp_path / "compliance_state.json"
    ny = datetime(2026, 9, 19, 10, 0)

    before = ComplianceEngine(config=_config(), trading_day_opens=_NY_OPEN)
    before.on_snapshot(
        object_id="v1",
        generated_at_utc=_SAVED_AT,
        now_ny=ny,
        account=_account(10000.0),
    )
    # Equity climbs, setting a higher peak, then gives some back.
    before.on_snapshot(
        object_id="v2",
        generated_at_utc=_SAVED_AT,
        now_ny=ny,
        account=_account(11000.0),
    )
    after_drawdown = before.on_snapshot(
        object_id="v3",
        generated_at_utc=_SAVED_AT,
        now_ny=ny,
        account=_account(10600.0),
    )
    assert after_drawdown.peak_equity == 11000.0

    save_compliance_state(
        path, before.persistent_state(account=_account(10600.0), saved_at_utc=_SAVED_AT)
    )

    restored = load_compliance_state(
        path, expected_login=5150234, expected_server="1xTrade-Server"
    )
    after = ComplianceEngine(
        config=_config(), trading_day_opens=_NY_OPEN, restored_state=restored
    )
    verdict = after.on_snapshot(
        object_id="v4",
        generated_at_utc=_SAVED_AT,
        now_ny=ny,
        account=_account(10600.0),
    )

    assert verdict.peak_equity == 11000.0, "the restarted engine forgot its peak"


def test_a_permanent_total_breach_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"
    ny = datetime(2026, 9, 19, 10, 0)

    before = ComplianceEngine(config=_config(), trading_day_opens=_NY_OPEN)
    before.on_snapshot(
        object_id="v1", generated_at_utc=_SAVED_AT, now_ny=ny, account=_account(10000.0)
    )
    breached = before.on_snapshot(
        object_id="v2", generated_at_utc=_SAVED_AT, now_ny=ny, account=_account(9000.0)
    )
    assert breached.status is ComplianceStatus.BREACHED_TOTAL

    save_compliance_state(
        path, before.persistent_state(account=_account(9000.0), saved_at_utc=_SAVED_AT)
    )
    restored = load_compliance_state(
        path, expected_login=5150234, expected_server="1xTrade-Server"
    )
    after = ComplianceEngine(
        config=_config(), trading_day_opens=_NY_OPEN, restored_state=restored
    )

    # Even with equity fully recovered, the breach stands.
    verdict = after.on_snapshot(
        object_id="v3", generated_at_utc=_SAVED_AT, now_ny=ny, account=_account(10000.0)
    )

    assert verdict.status is ComplianceStatus.BREACHED_TOTAL
    assert verdict.allowed is False


def test_the_day_anchor_survives_a_restart_on_the_same_trading_day(tmp_path: Path) -> None:
    """The deliberate divergence from PropFirmGuard: a mid-day restart
    must not re-anchor day-start equity and grant a second full daily
    allowance."""
    path = tmp_path / "compliance_state.json"
    ny = datetime(2026, 9, 19, 10, 0)

    before = ComplianceEngine(config=_config(), trading_day_opens=_NY_OPEN)
    before.on_snapshot(
        object_id="v1", generated_at_utc=_SAVED_AT, now_ny=ny, account=_account(10000.0)
    )
    save_compliance_state(
        path, before.persistent_state(account=_account(9600.0), saved_at_utc=_SAVED_AT)
    )

    restored = load_compliance_state(
        path, expected_login=5150234, expected_server="1xTrade-Server"
    )
    after = ComplianceEngine(
        config=_config(), trading_day_opens=_NY_OPEN, restored_state=restored
    )
    verdict = after.on_snapshot(
        object_id="v2",
        generated_at_utc=_SAVED_AT,
        now_ny=datetime(2026, 9, 19, 11, 0),
        account=_account(9600.0),
    )

    assert verdict.day_start_equity == 10000.0, "the day anchor was re-anchored on restart"
    assert verdict.daily_loss_used_fraction > 0.0


def test_a_restart_on_a_later_trading_day_re_anchors_normally(tmp_path: Path) -> None:
    path = tmp_path / "compliance_state.json"

    before = ComplianceEngine(config=_config(), trading_day_opens=_NY_OPEN)
    before.on_snapshot(
        object_id="v1",
        generated_at_utc=_SAVED_AT,
        now_ny=datetime(2026, 9, 19, 10, 0),
        account=_account(10000.0),
    )
    save_compliance_state(
        path, before.persistent_state(account=_account(9600.0), saved_at_utc=_SAVED_AT)
    )

    restored = load_compliance_state(
        path, expected_login=5150234, expected_server="1xTrade-Server"
    )
    after = ComplianceEngine(
        config=_config(), trading_day_opens=_NY_OPEN, restored_state=restored
    )
    verdict = after.on_snapshot(
        object_id="v2",
        generated_at_utc=_SAVED_AT,
        now_ny=datetime(2026, 9, 22, 10, 0),
        account=_account(9600.0),
    )

    assert verdict.day_start_equity == 9600.0, "a new trading day should re-anchor"
    assert verdict.daily_loss_used_fraction == 0.0
    # The peak, unlike the day anchor, is NOT day-scoped and still stands.
    assert verdict.peak_equity == 10000.0


def test_a_fresh_engine_with_no_saved_state_behaves_as_before(tmp_path: Path) -> None:
    """restored_state=None must change nothing -- every existing call site
    passes no state at all."""
    engine = ComplianceEngine(config=_config(), trading_day_opens=_NY_OPEN, restored_state=None)

    verdict = engine.on_snapshot(
        object_id="v1",
        generated_at_utc=_SAVED_AT,
        now_ny=datetime(2026, 9, 19, 10, 0),
        account=_account(10000.0),
    )

    assert verdict.status is ComplianceStatus.SAFE
    assert verdict.day_start_equity == 10000.0
    assert verdict.peak_equity == 10000.0
