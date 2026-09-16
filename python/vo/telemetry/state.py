"""
RuntimeState -- the EA's own telemetry snapshot (draft, schema_version 0).

Layer 7: the top of the stack. Telemetry exists to *summarize* what every
other layer has observed, so it is allowed to import all of them -- nothing
in `vo` may import telemetry back, and nothing outside `vo` except the
dashboard may import it at all. This mirrors the architecture audit's R11
mitigation almost exactly: "RuntimeState publishing is fire-and-forget; an
architecture test forbids vo.runtime importing dashboard." The test that
enforces the `vo`-side half of that lives in tests/unit/test_architecture.py
as test_vo_does_not_import_dashboard.

STATUS: draft, not the frozen Phase 20 contract. Phase 10 (`VO_EA` v1) is
the first thing that will ever construct a RuntimeState from real data, and
Phase 20 ("Telemetry RuntimeState . monitoring . alert manager" in
architecture/vo-phase-plan.md) is where this schema is meant to be
finalized and versioned per G4. This module exists earlier than that, on
its own, for one reason: the dashboard (nominally Phase 22, scaffolded
early as a parallel track -- see vo-phase-plan.md SS10a) needs a real wire
contract to code against today, and the project's standing rule is that a
screen never shows a fabricated number. `disconnected_state()` is the only
RuntimeState this module will ever construct on its own; every other one
must come from a real EA process.

Expect non-trivial growth before Phase 20 closes -- eventually a summary of
MarketState, TimeContext, RegimeState, account/position state, and
health/alert information, per the audit's future-components table. Fields
should only ever be added, never renamed or repurposed once a real producer
and the dashboard both depend on them -- that is exactly the drift G4
exists to prevent, and `schema_version` is the field a consumer checks
before trusting anything else about shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

CURRENT_SCHEMA_VERSION = 0
"""Draft. Bump this, deliberately, the first time a field is added, renamed,
or removed -- never silently repurpose a field under the same version."""


class ConnectionStatus(Enum):
    """What the dashboard actually knows about the EA process right now."""

    DISCONNECTED = auto()
    """No EA process is publishing RuntimeState. The only status this
    schema is allowed to start in -- never inferred as CONNECTED merely
    because a socket accepted a TCP handshake."""
    CONNECTED = auto()
    """An EA process is actively publishing RuntimeState updates."""
    STALE = auto()
    """An EA process connected at some point, but no update has arrived
    within the configured staleness window. Kept distinct from
    DISCONNECTED because the last known state may still be worth showing
    -- labeled stale, never silently treated as current."""


@dataclass(frozen=True)
class RuntimeState:
    """One snapshot of what the EA process is doing, as of `emitted_at`."""

    schema_version: int
    connection: ConnectionStatus
    emitted_at: datetime | None
    """None only when connection is DISCONNECTED and nothing has ever
    arrived -- never a placeholder time standing in for "unknown"."""
    ea_phase: str | None = None
    """Which phase's build the connected EA process is running, e.g. "10"
    once VO_EA v1 exists. None until a real EA reports one."""
    detail: str | None = None
    """Free-text, human-readable context for the current status -- e.g. why
    a connection attempt failed. Never a channel for smuggling a number
    that should have its own typed field instead."""


def disconnected_state(detail: str | None = None) -> RuntimeState:
    """
    The one honest default: "nothing has connected." Exists so every
    caller expresses this the same way instead of hand-rolling it, and so
    there is exactly one place that says schema_version 0 means draft.
    """

    return RuntimeState(
        schema_version=CURRENT_SCHEMA_VERSION,
        connection=ConnectionStatus.DISCONNECTED,
        emitted_at=None,
        detail=detail,
    )
