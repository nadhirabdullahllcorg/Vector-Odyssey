from dataclasses import dataclass

from .identity import InstrumentId


@dataclass(frozen=True)
class Symbol:
    """
    Canonical representation of a tradable market symbol.

    This object represents instrument metadata only.
    It contains no trading interpretation or strategy logic.
    """

    broker_symbol: str
    description: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    source: str
    instrument_id: InstrumentId | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("Symbol source cannot be empty")
        if not self.broker_symbol.strip():
            raise ValueError("Symbol broker_symbol cannot be empty")
        if self.digits < 0:
            raise ValueError("Symbol digits cannot be negative")
        if self.point <= 0:
            raise ValueError("Symbol point must be greater than zero")
        if self.tick_size <= 0:
            raise ValueError("Symbol tick_size must be greater than zero")
