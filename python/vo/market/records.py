from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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
    last: Optional[float]
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