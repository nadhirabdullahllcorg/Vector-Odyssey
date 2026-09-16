"""
Structured instrument identity.

Split into its own module (rather than living in mapping.py) so that the
domain objects (Bar/Tick/Symbol) can carry an `instrument_id` field without
importing anything from the mapping/quarantine layer that in turn imports
them — that would be a cycle. `vo.market.identity` depends on nothing else
in this package; `bar.py`/`tick.py`/`symbol.py` and `mapping.py` both import
from it.

See architecture/vo-architecture-audit.md, finding E.5: "the broker symbol
*is* the identity" today — a `source` string like "MT5:US100.n", or a raw
`broker_symbol` field, is not structured data a strategy can safely key on,
and §36 requires broker-specific symbol names not leak into strategy logic.
InstrumentId is the narrow fix: a stable, structured handle, resolved once
at the mapping boundary (see mapping.resolve_instrument_id) instead of
re-parsed wherever an identity check is needed.

This is deliberately not a catalog or config-backed resolver yet — just the
value type and the parsing rule needed to produce one honestly from what
the wire records actually carry today, v1 and v2 alike. A real catalog
lookup is a later phase's job; this module gives it exactly one type to
target.
"""

from __future__ import annotations

from dataclasses import dataclass

UNKNOWN_BROKER_SERVER = "UNKNOWN"
"""
Sentinel for InstrumentId.broker_server when the source record has no way
to say what broker server it came from — schema v1's fused `source` string
("MT5:US100.n") never carried one. This makes the gap visible in the value
itself instead of inventing a server name or silently dropping the field.
"""


@dataclass(frozen=True)
class InstrumentId:
    """
    A structured, provenance-carrying instrument identity.

    Always three parts even though v1 sources only ever yield two
    (platform, broker_symbol) — see UNKNOWN_BROKER_SERVER. Inventing a
    separate two-field shape for v1 would just move the leak from "broker
    symbol as identity" to "which InstrumentId shape am I holding".
    """

    platform: str
    broker_server: str
    broker_symbol: str

    def __post_init__(self) -> None:
        if not self.platform.strip():
            raise ValueError("InstrumentId platform cannot be empty")
        if not self.broker_server.strip():
            raise ValueError("InstrumentId broker_server cannot be empty")
        if not self.broker_symbol.strip():
            raise ValueError("InstrumentId broker_symbol cannot be empty")

    @property
    def key(self) -> str:
        """A single stable string, e.g. for use as a dict/log key."""
        return f"{self.platform}:{self.broker_server}:{self.broker_symbol}"
