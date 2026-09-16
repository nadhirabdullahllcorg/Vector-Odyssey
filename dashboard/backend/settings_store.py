"""
SettingsStore -- a placeholder for the "VO <-> EA versioned settings schema"
named in architecture/vo-phase-plan.md SS1, where the dashboard's
per-concept enable/disable toggles are meant to live.

That schema does not exist yet -- no phase has defined its shape, because
there are no VO concepts to toggle until Phase 8+ produces some. Rather than
invent a plausible-looking shape now and risk it drifting from whatever
Phase 8/13/32 actually need, this store is honest about being empty: it
always reports an empty settings document and a null schema version, and
writes are refused with a clear reason. It exists purely so the dashboard's
REST surface and frontend have a real, stable endpoint to build against
today -- the endpoint's behavior changes the moment a real settings schema
exists to serve; its URL does not.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsSnapshot:
    schema_version: int | None
    settings: dict[str, object]
    note: str


class SettingsNotYetDefinedError(RuntimeError):
    """Raised by write attempts until a real settings schema exists."""


class SettingsStore:
    """In-memory placeholder. Deliberately has no persistence yet -- there
    is nothing worth persisting until a real schema exists to fill it."""

    _NOTE = (
        "No versioned settings schema exists yet (see vo-phase-plan.md SS1 / "
        "SS10a) -- there are no VO concepts to toggle until Phase 8+ defines "
        "some. This endpoint is a stable placeholder, not mocked data."
    )

    def read(self) -> SettingsSnapshot:
        return SettingsSnapshot(schema_version=None, settings={}, note=self._NOTE)

    def write(self, _settings: dict[str, object]) -> None:
        raise SettingsNotYetDefinedError(self._NOTE)
