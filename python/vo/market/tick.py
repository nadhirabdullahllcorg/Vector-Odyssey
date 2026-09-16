from dataclasses import dataclass
from datetime import datetime

from .identity import InstrumentId


@dataclass(frozen=True)
class Tick:
    """
    Canonical representation of a single market tick.

    This object represents market data only.
    It contains no trading interpretation or strategy logic.
    """

    timestamp: datetime
    bid: float
    ask: float
    last: float | None
    volume: float
    source: str
    instrument_id: InstrumentId | None = None

    def __post_init__(self) -> None:
        if self.volume < 0:
            raise ValueError("Tick volume cannot be negative")
        if self.ask < self.bid:
            raise ValueError("Tick ask cannot be below tick bid")
        if self.timestamp.tzinfo is None:
            raise ValueError("Tick timestamp must be timezone-aware")