"""
Timeframe — one typed vocabulary instead of two.

Fixes audit finding C4 (architecture/vo-candle-layer.md §8): `Bar.timeframe`
was a free `str`, and the repo already had two spellings in circulation —
the short form ("M1", "M5" — tests) and MT5's own enum spelling
("PERIOD_M1" — the bridge, via EnumToString()). "M1" != "PERIOD_M1" would
silently partition any grouping, join, or cache key.

The wire keeps whatever MT5 sends (`BarRecordV2.timeframe: str`, unchanged).
`Timeframe.from_mt5()` is the one place either spelling is turned into a
member — nothing above the mapper should ever compare against a raw string
again.
"""

from __future__ import annotations

from enum import Enum, auto

_MT5_ALIASES: dict[str, str] = {
    # MT5 EnumToString() spelling -> canonical short form.
    "PERIOD_M1": "M1",
    "PERIOD_M5": "M5",
    "PERIOD_M15": "M15",
    "PERIOD_M30": "M30",
    "PERIOD_H1": "H1",
    "PERIOD_H4": "H4",
    "PERIOD_D1": "D1",
    "PERIOD_W1": "W1",
    "PERIOD_MN1": "MN1",
    # The short form already circulates too (tests/unit/test_bar.py,
    # test_records.py) — accept it directly rather than rejecting real,
    # already-observed input.
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4",
    "D1": "D1",
    "W1": "W1",
    "MN1": "MN1",
}

_SECONDS: dict[str, int | None] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    # A month has no fixed length in seconds, and CUSTOM has none at all by
    # definition. Both are None rather than a wrong-most-of-the-time guess
    # (28/30/31 days) — the same "expose what is not known" principle as
    # C3's zero-range None.
    "MN1": None,
    "CUSTOM": None,
}


class Timeframe(Enum):
    M1 = auto()
    M5 = auto()
    M15 = auto()
    M30 = auto()
    H1 = auto()
    H4 = auto()
    D1 = auto()
    W1 = auto()
    MN1 = auto()
    CUSTOM = auto()

    @property
    def canonical(self) -> str:
        """The short-form spelling, e.g. "M1". Never MT5's PERIOD_* form."""
        return self.name

    @property
    def seconds(self) -> int | None:
        """Fixed duration in seconds, or None when there isn't one (see _SECONDS)."""
        return _SECONDS[self.name]

    @classmethod
    def from_mt5(cls, value: str) -> Timeframe:
        """
        Map either spelling in circulation today ("PERIOD_M1" from the
        bridge, "M1" from hand fixtures/tests) onto one member. Raises on
        anything else rather than guessing — an unrecognized timeframe
        string is a fact worth surfacing, not silently coercing.
        """
        canonical = _MT5_ALIASES.get(value)

        if canonical is None:
            raise ValueError(f"Unrecognized timeframe: {value!r}")

        return cls[canonical]
