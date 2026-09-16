"""
CanonicalRecord / AppendOnlyLog (Phase 8) -- bitemporal fields, version
stamps, and the append-only discipline gates G4/G5 require.

No real canonical object exists yet (Phase 11's swings, Phase 13's
RegimeState, and so on all come later), so this file proves the contract
itself is sound the same way test_architecture.py's
test_the_hypothesis_checker_actually_catches_a_violation proves G2's
checker works before any decision path exists: with a toy example.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta

import pytest

from vo.interfaces.canonical import AppendOnlyLog, CanonicalRecord, CanonicalRecordError

_T0 = datetime(2026, 9, 10, 14, 32, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=5)


@dataclass(frozen=True)
class _ExampleRecord(CanonicalRecord):
    """A toy canonical record, standing in for a real one (e.g. RegimeState)
    that a later phase will define on top of this same contract."""

    value: str = ""


def _record(**overrides: object) -> _ExampleRecord:
    defaults: dict[str, object] = {
        "object_type": "EXAMPLE",
        "object_id": "EXAMPLE-000001",
        "observed_at": _T0,
        "recorded_at": _T1,
        "methodology_version": 0,
    }
    defaults.update(overrides)
    return _ExampleRecord(**defaults)  # type: ignore[arg-type]


# ── bitemporal fields ────────────────────────────────────────────────────


def test_recorded_at_may_equal_observed_at() -> None:
    record = _record(observed_at=_T0, recorded_at=_T0)
    assert record.observed_at == record.recorded_at


def test_recorded_at_before_observed_at_is_rejected() -> None:
    with pytest.raises(CanonicalRecordError, match="recorded_at cannot be before"):
        _record(observed_at=_T1, recorded_at=_T0)


def test_recorded_at_may_be_much_later_than_observed_at() -> None:
    """Replay/backtest: observed_at is historical, recorded_at is whenever
    the replay run actually happened -- which can be years later."""
    replay_time = _T0 + timedelta(days=400)
    record = _record(observed_at=_T0, recorded_at=replay_time)
    assert record.recorded_at == replay_time


# ── version stamps ───────────────────────────────────────────────────────


def test_methodology_version_is_required() -> None:
    with pytest.raises(TypeError):
        _ExampleRecord(  # type: ignore[call-arg]
            object_type="EXAMPLE",
            object_id="EXAMPLE-000001",
            observed_at=_T0,
            recorded_at=_T0,
        )


def test_negative_methodology_version_is_rejected() -> None:
    with pytest.raises(CanonicalRecordError, match="methodology_version"):
        _record(methodology_version=-1)


def test_record_is_frozen_so_a_version_cannot_drift_after_construction() -> None:
    record = _record()

    with pytest.raises(FrozenInstanceError):
        record.methodology_version = 1  # type: ignore[misc]


# ── identity / supersession ──────────────────────────────────────────────


def test_blank_object_type_is_rejected() -> None:
    with pytest.raises(CanonicalRecordError, match="object_type"):
        _record(object_type="   ")


def test_blank_object_id_is_rejected() -> None:
    with pytest.raises(CanonicalRecordError, match="object_id"):
        _record(object_id="")


def test_a_record_cannot_supersede_itself() -> None:
    with pytest.raises(CanonicalRecordError, match="supersede itself"):
        _record(object_id="EXAMPLE-000001", supersedes="EXAMPLE-000001")


def test_an_original_observation_supersedes_nothing() -> None:
    assert _record().supersedes is None


# ── AppendOnlyLog ─────────────────────────────────────────────────────────


def test_log_starts_empty() -> None:
    log: AppendOnlyLog[_ExampleRecord] = AppendOnlyLog()
    assert len(log) == 0
    assert log.all() == ()


def test_append_then_get_by_object_id() -> None:
    log: AppendOnlyLog[_ExampleRecord] = AppendOnlyLog()
    original = _record(object_id="EXAMPLE-000001")
    log.append(original)

    assert log.get("EXAMPLE-000001") is original
    assert log.get("EXAMPLE-999999") is None
    assert len(log) == 1


def test_a_correction_is_a_new_record_that_supersedes_the_old_one() -> None:
    log: AppendOnlyLog[_ExampleRecord] = AppendOnlyLog()
    original = _record(object_id="EXAMPLE-000001", value="first read")
    correction = _record(
        object_id="EXAMPLE-000002",
        value="corrected read",
        supersedes="EXAMPLE-000001",
        recorded_at=_T1 + timedelta(minutes=10),
    )

    log.append(original)
    log.append(correction)

    assert len(log) == 2
    assert log.get("EXAMPLE-000001") == original
    assert log.get("EXAMPLE-000002") == correction
    assert log.superseded_by("EXAMPLE-000001") == correction
    assert log.is_current("EXAMPLE-000001") is False
    assert log.is_current("EXAMPLE-000002") is True


def test_an_uncorrected_record_is_current() -> None:
    log: AppendOnlyLog[_ExampleRecord] = AppendOnlyLog()
    log.append(_record())

    assert log.is_current("EXAMPLE-000001") is True


def test_an_unknown_object_id_is_not_current() -> None:
    log: AppendOnlyLog[_ExampleRecord] = AppendOnlyLog()
    assert log.is_current("EXAMPLE-DOES-NOT-EXIST") is False


def test_the_log_exposes_no_update_or_delete_method() -> None:
    """Gate G5, mechanically: corrections append, they never rewrite. If a
    future edit ever adds update()/delete() back to AppendOnlyLog, this
    test is what should catch it."""
    log: AppendOnlyLog[_ExampleRecord] = AppendOnlyLog()

    assert not hasattr(log, "update")
    assert not hasattr(log, "delete")
    assert not hasattr(log, "remove")
    assert not hasattr(log, "__setitem__")
    assert not hasattr(log, "__delitem__")
