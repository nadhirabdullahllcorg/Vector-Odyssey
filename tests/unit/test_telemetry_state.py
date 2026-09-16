"""
vo.telemetry.state -- the RuntimeState draft schema never fabricates a
connection it doesn't have.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from vo.telemetry.state import (
    CURRENT_SCHEMA_VERSION,
    ConnectionStatus,
    RuntimeState,
    disconnected_state,
)


def test_disconnected_state_is_honest_about_knowing_nothing() -> None:
    state = disconnected_state()

    assert state.connection is ConnectionStatus.DISCONNECTED
    assert state.emitted_at is None
    assert state.ea_phase is None


def test_disconnected_state_carries_the_draft_schema_version() -> None:
    assert disconnected_state().schema_version == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 0


def test_disconnected_state_can_carry_a_reason() -> None:
    state = disconnected_state(detail="connection refused: no EA listening")
    assert state.detail == "connection refused: no EA listening"


def test_a_connected_state_can_report_which_ea_phase_is_running() -> None:
    state = RuntimeState(
        schema_version=CURRENT_SCHEMA_VERSION,
        connection=ConnectionStatus.CONNECTED,
        emitted_at=datetime.now(UTC),
        ea_phase="10",
    )

    assert state.connection is ConnectionStatus.CONNECTED
    assert state.ea_phase == "10"
    assert state.emitted_at is not None


def test_runtime_state_is_frozen() -> None:
    state = disconnected_state()

    with pytest.raises(FrozenInstanceError):
        state.connection = ConnectionStatus.CONNECTED  # type: ignore[misc]
