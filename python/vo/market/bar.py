from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    """
    Canonical representation of a single market bar.

    This object represents market data only.
    It contains no trading interpretation or strategy logic.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    real_volume: int
    timeframe: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Bar timestamp must be timezone-aware")
        if self.tick_volume < 0:
            raise ValueError("Bar tick_volume cannot be negative")
        if not self.timeframe.strip():
            raise ValueError("Bar timeframe cannot be empty")
        if self.high < self.low:
            raise ValueError("Bar high cannot be below bar low")
        if self.open > self.high:
            raise ValueError("Bar open cannot be above bar high")
        if self.open < self.low:
            raise ValueError("Bar open cannot be below bar low")
        if self.close > self.high:
            raise ValueError("Bar close cannot be above bar high")
        if self.close < self.low:
            raise ValueError("Bar close cannot be below bar low")
    