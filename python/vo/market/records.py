from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TickRecord:
    """
    Canonical wire-format representation of one MT5 tick.

    This is an interchange contract only.
    It contains raw market data and no strategy interpretation.
    """

    timestamp_ms: int
    bid: float
    ask: float
    last: float | None
    volume: float
    source: str


@dataclass(frozen=True)
class BarRecord:
    """
    Canonical wire-format representation of one MT5 bar.

    This is an interchange contract only.
    It contains raw market data and no strategy interpretation.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    real_volume: int
    timeframe: str
    source: str


@dataclass(frozen=True)
class SymbolRecord:
    """
    Canonical wire-format representation of MT5 symbol metadata.

    This is an interchange contract only.
    It contains instrument metadata and no strategy interpretation.
    """

    broker_symbol: str
    description: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    source: str

# ── v2 — Phase 3 ─────────────────────────────────────────────────────────
#
# See schema.py's SCHEMA_V2 section for why these differ from the v1 records
# above: no silent server->UTC conversion (F2), decomposed source, and the
# spread/seq/flags/volume_real/source_feed fields the Phase 3 bridge adds.
# The v1 dataclasses above are kept, unchanged, as a historical record of
# what the pre-Phase-3 bridges actually produced.


@dataclass(frozen=True)
class TickRecordV2:
    """
    Canonical wire-format representation of one tick, schema v2.

    This is an interchange contract only. It contains raw market data and no
    strategy interpretation. `timestamp_server_ms` is broker server
    wall-clock, not UTC — see schema.py.
    """

    timestamp_server_ms: int
    bid: float
    ask: float
    last: float | None
    volume: float
    volume_real: float
    flags: int
    seq: int
    source_feed: str
    platform: str
    broker_server: str
    broker_symbol: str


@dataclass(frozen=True)
class BarRecordV2:
    """
    Canonical wire-format representation of one bar, schema v2.

    This is an interchange contract only. It contains raw market data and no
    strategy interpretation. `timestamp` is broker server wall-clock, not
    UTC — see schema.py.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    real_volume: int
    spread: int
    timeframe: str
    seq: int
    source_feed: str
    platform: str
    broker_server: str
    broker_symbol: str


@dataclass(frozen=True)
class SymbolRecordV2:
    """
    Canonical wire-format representation of symbol metadata, schema v2.

    This is an interchange contract only. It contains instrument metadata
    and no strategy interpretation.
    """

    broker_symbol: str
    description: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    platform: str
    broker_server: str


@dataclass(frozen=True)
class SourceCapabilitiesRecord:
    """
    What a (platform, broker_server, broker_symbol) source has been observed
    to provide, recorded once rather than inferred per-record.

    real_volume_available and tick_level_available are evidence-based lower
    bounds (see schema.py's SOURCE_CAPABILITIES_V1 note), not guarantees.
    """

    platform: str
    broker_server: str
    broker_symbol: str
    real_volume_available: bool
    tick_level_available: bool
