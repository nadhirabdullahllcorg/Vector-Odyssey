"""
Wire records -> canonical domain objects, with instrument identity attached.

Two audit findings meet here (architecture/vo-architecture-audit.md):

    E.5  No instrument identity — the broker symbol *is* the identity.
         Fixed by resolve_instrument_id / InstrumentId (vo.market.identity).

    E.7  Validation-by-exception makes a bad observation unrepresentable.
         `Bar.__post_init__` (and Tick's, and Symbol's) still raise on an
         invariant violation — that is not weakened here. What changes is
         the *ingest* policy: map_records partitions a batch into accepted
         domain objects and quarantined (record, reason) pairs instead of
         one bad record aborting the whole run.

Scope, deliberately narrow (see architecture/vo-candle-layer.md's own
Phase 4 vs Phase 5 split): this is the mapper and the quarantine boundary,
not the richer future Bar shape (bar_id, provenance, quality, temporal
context) — that is Phase 5. It also does not attempt UTC time resolution
for schema v2 records; see UnresolvedServerTimeError below for why that is
a deliberate refusal, not an oversight.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from .bar import Bar
from .deserialization import AnyRecord
from .identity import UNKNOWN_BROKER_SERVER, InstrumentId
from .records import (
    BarRecord,
    BarRecordV2,
    SourceCapabilitiesRecord,
    SymbolRecord,
    SymbolRecordV2,
    TickRecord,
    TickRecordV2,
)
from .symbol import Symbol
from .tick import Tick

__all__ = [
    "InstrumentId",
    "MappingResult",
    "QuarantinedRecord",
    "UnresolvedServerTimeError",
    "map_records",
    "record_to_domain",
    "resolve_instrument_id",
]


class UnresolvedServerTimeError(ValueError):
    """
    Raised when a schema-v2 record's timestamp is broker server wall-clock
    with no established UTC offset yet.

    TickRecordV2.timestamp_server_ms and BarRecordV2.timestamp are both
    documented (schema.py) as server wall-clock, explicitly NOT UTC — that
    is what F2 was: a previous silent, PC-derived server->UTC conversion.
    Resolving them honestly requires the Time Engine (Phase 6), which
    consumes the DST calendar captured in config/settings/brokers.yaml
    (see vo.time.probe). Phase 4 does not invent a substitute conversion
    here — that would reintroduce exactly the defect v2 exists to stop.
    map_records() catches this and quarantines the record with this
    reason instead of letting it raise past the batch boundary.
    """


def _parse_v1_source(source: str) -> tuple[str, str, str]:
    """
    v1's fused `source` is "PLATFORM:SYMBOL" for tick/bar records (e.g.
    "MT5:US100.n" — see TICK_V1/BAR_V1 in schema.py). No broker_server
    travels over the wire in v1 at all, so it cannot be recovered here;
    it is filled in as UNKNOWN_BROKER_SERVER rather than guessed.
    """
    platform, sep, symbol = source.partition(":")

    if not sep or not platform.strip() or not symbol.strip():
        raise ValueError(
            f"v1 source {source!r} is not in PLATFORM:SYMBOL form — cannot "
            f"resolve an instrument identity from it"
        )

    return platform, UNKNOWN_BROKER_SERVER, symbol


def _identity_parts(record: AnyRecord) -> tuple[str, str, str]:
    """
    The wire-shape-specific part of identity resolution. v1 SymbolRecord is
    its own case: `source` there is platform alone ("MT5") and
    `broker_symbol` is already its own explicit field — unlike v1
    tick/bar records, nothing needs parsing apart.
    """
    if isinstance(record, TickRecordV2 | BarRecordV2 | SymbolRecordV2 | SourceCapabilitiesRecord):
        return record.platform, record.broker_server, record.broker_symbol

    if isinstance(record, SymbolRecord):
        if not record.source.strip():
            raise ValueError("SymbolRecord.source (platform) cannot be empty")
        return record.source, UNKNOWN_BROKER_SERVER, record.broker_symbol

    if isinstance(record, TickRecord | BarRecord):
        return _parse_v1_source(record.source)

    raise TypeError(f"No instrument identity mapping for {type(record).__name__}")


def resolve_instrument_id(record: AnyRecord) -> InstrumentId:
    """
    The one place in vo.market that turns a wire record's provenance
    fields into a structured InstrumentId. Every mapping in this module
    goes through it, so a future real catalog/config-backed resolver has
    exactly one call site to change.
    """
    platform, broker_server, broker_symbol = _identity_parts(record)
    return InstrumentId(
        platform=platform,
        broker_server=broker_server,
        broker_symbol=broker_symbol,
    )


def record_to_domain(record: AnyRecord) -> Tick | Bar | Symbol:
    """
    Map one wire record onto its domain object, attaching a resolved
    InstrumentId.

    Raises exactly as constructing the domain object directly would — this
    is a drop-in replacement for hand-building a Tick/Bar/Symbol from a
    record's fields, not a new validation policy. It also raises
    UnresolvedServerTimeError for the v2 tick/bar records this phase
    cannot honestly time-resolve yet. Batch-level quarantining lives in
    map_records, not here.
    """
    instrument_id = resolve_instrument_id(record)

    if isinstance(record, SymbolRecord):
        return Symbol(
            broker_symbol=record.broker_symbol,
            description=record.description,
            digits=record.digits,
            point=record.point,
            tick_size=record.tick_size,
            tick_value=record.tick_value,
            contract_size=record.contract_size,
            source=record.source,
            instrument_id=instrument_id,
        )

    if isinstance(record, SymbolRecordV2):
        return Symbol(
            broker_symbol=record.broker_symbol,
            description=record.description,
            digits=record.digits,
            point=record.point,
            tick_size=record.tick_size,
            tick_value=record.tick_value,
            contract_size=record.contract_size,
            source=record.platform,
            instrument_id=instrument_id,
        )

    if isinstance(record, TickRecord):
        # TICK_V1.timestamp_ms is documented in schema.py as UTC epoch
        # milliseconds, unlike v2's server-wall-clock field below — this
        # is the wire contract's own claim, not an assumption made here.
        return Tick(
            timestamp=datetime.fromtimestamp(record.timestamp_ms / 1000, tz=UTC),
            bid=record.bid,
            ask=record.ask,
            last=record.last,
            volume=record.volume,
            source=record.source,
            instrument_id=instrument_id,
        )

    if isinstance(record, TickRecordV2):
        raise UnresolvedServerTimeError(
            "TickRecordV2.timestamp_server_ms is broker server wall-clock, "
            "not UTC (schema.py TICK_V2, F2) — resolving it to a real "
            "instant is the Time Engine's job (Phase 6), not this mapper's."
        )

    if isinstance(record, BarRecord):
        return Bar(
            timestamp=record.timestamp,
            open=record.open,
            high=record.high,
            low=record.low,
            close=record.close,
            tick_volume=record.tick_volume,
            real_volume=record.real_volume,
            timeframe=record.timeframe,
            instrument_id=instrument_id,
        )

    if isinstance(record, BarRecordV2):
        raise UnresolvedServerTimeError(
            "BarRecordV2.timestamp is broker server wall-clock, not UTC "
            "(schema.py's v2 module note, F2) — resolving it to a real "
            "instant is the Time Engine's job (Phase 6), not this mapper's."
        )

    if isinstance(record, SourceCapabilitiesRecord):
        raise TypeError(
            "SourceCapabilitiesRecord has no domain-object counterpart — "
            "it describes a source's observed capabilities, not an "
            "observation itself"
        )

    raise TypeError(f"No domain mapping for {type(record).__name__}")


@dataclass(frozen=True)
class QuarantinedRecord:
    """One record map_records could not accept, and why."""

    record: AnyRecord
    reason: str


@dataclass(frozen=True)
class MappingResult:
    """The outcome of mapping a batch: what was accepted, what was set aside."""

    accepted: tuple[Tick | Bar | Symbol, ...]
    quarantined: tuple[QuarantinedRecord, ...]

    @property
    def all_accepted(self) -> bool:
        return not self.quarantined


def map_records(records: Iterable[AnyRecord]) -> MappingResult:
    """
    Map a batch of wire records to domain objects.

    Per E.7: a record that fails — a domain invariant, an unresolvable
    identity, an unresolvable v2 timestamp — is quarantined with that
    exact error message as its reason, instead of aborting every record
    after it. Domain invariants themselves are not weakened; this only
    changes what happens to the *ingest* when one is violated.
    """
    accepted: list[Tick | Bar | Symbol] = []
    quarantined: list[QuarantinedRecord] = []

    for record in records:
        try:
            accepted.append(record_to_domain(record))
        except (ValueError, TypeError) as exc:
            quarantined.append(QuarantinedRecord(record=record, reason=str(exc)))

    return MappingResult(accepted=tuple(accepted), quarantined=tuple(quarantined))
