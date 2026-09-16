"""
Resolving Phase 4's UnresolvedServerTimeError for schema-v2 tick/bar
records - the gap vo.market.mapping deliberately left open, since actually
resolving it needs vo.time (layer 2), which vo.market (layer 1) may not
import.

vo.market.mapping.record_to_domain still raises UnresolvedServerTimeError
for TickRecordV2/BarRecordV2, completely unchanged - that stays correct at
its own layer, forever: vo.market genuinely cannot resolve real time on its
own. `resolve_record` here is what actually resolves them, given a broker
profile: it delegates everything else (v1 records, symbols) straight to
vo.market.mapping.record_to_domain, and only intervenes for the two record
types that raise.
"""

from __future__ import annotations

from datetime import UTC, datetime

from vo.market.bar import Bar, DataQuality
from vo.market.deserialization import AnyRecord
from vo.market.mapping import (
    UnresolvedServerTimeError,
    record_to_domain,
    resolve_instrument_id,
)
from vo.market.records import BarRecordV2, TickRecordV2
from vo.market.symbol import Symbol
from vo.market.tick import Tick
from vo.market.timeframe import Timeframe
from vo.time.brokers import BrokerProfile, make_wire_provenance, resolve_broker_utc
from vo.time.context import TemporalStatus


def _server_ms_to_naive(ms: int) -> datetime:
    """MqlTick.time_msc: epoch milliseconds computed from the broker's own
    (possibly non-UTC) clock, not true UTC epoch ms - see schema.py's
    TICK_V2 note. Recovering the intended wall-clock fields is the same
    trick _parse_server_timestamp uses for BarRecordV2's string field."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)


def resolve_record(
    record: AnyRecord, broker_profiles: dict[str, BrokerProfile]
) -> Tick | Bar | Symbol:
    """
    record_to_domain, extended to actually resolve schema-v2 tick/bar
    timestamps instead of raising, when a profile for that record's
    broker_server exists in config/settings/brokers.yaml.

    Raises UnresolvedServerTimeError unchanged when no profile is
    available - resolving with an unknown broker's rules would be exactly
    the guess this whole phase exists to refuse.
    """
    try:
        return record_to_domain(record)
    except UnresolvedServerTimeError:
        pass

    if isinstance(record, TickRecordV2):
        return _resolve_tick_v2(record, broker_profiles)

    if isinstance(record, BarRecordV2):
        return _resolve_bar_v2(record, broker_profiles)

    raise UnresolvedServerTimeError(f"No time resolution defined for {type(record).__name__}")


def _profile_or_raise(
    broker_server: str, broker_profiles: dict[str, BrokerProfile]
) -> BrokerProfile:
    profile = broker_profiles.get(broker_server)
    if profile is None:
        raise UnresolvedServerTimeError(
            f"No broker profile for {broker_server!r} in config/settings/brokers.yaml "
            f"- cannot resolve this record's server wall-clock timestamp"
        ) from None
    return profile


def _resolve_tick_v2(record: TickRecordV2, broker_profiles: dict[str, BrokerProfile]) -> Tick:
    profile = _profile_or_raise(record.broker_server, broker_profiles)
    naive = _server_ms_to_naive(record.timestamp_server_ms)
    resolution = resolve_broker_utc(naive, profile)
    instrument_id = resolve_instrument_id(record)

    return Tick(
        timestamp=resolution.utc,
        bid=record.bid,
        ask=record.ask,
        last=record.last,
        volume=record.volume,
        source=f"{record.platform}:{record.broker_symbol}",
        instrument_id=instrument_id,
    )


def _resolve_bar_v2(record: BarRecordV2, broker_profiles: dict[str, BrokerProfile]) -> Bar:
    profile = _profile_or_raise(record.broker_server, broker_profiles)
    resolution = resolve_broker_utc(record.timestamp, profile)
    instrument_id = resolve_instrument_id(record)

    provenance = make_wire_provenance(
        schema_version=2,
        platform=record.platform,
        broker_server=record.broker_server,
        raw_broker_timestamp=record.timestamp.isoformat(),
        profile=profile,
        resolution=resolution,
    )

    quality = DataQuality.VALID
    quality_reason = None
    if resolution.status is not TemporalStatus.VALID:
        quality = DataQuality.SUSPECT
        quality_reason = f"Time resolution status: {resolution.status.name}"

    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.from_mt5(record.timeframe),
        open_time_utc=resolution.utc,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        tick_volume=record.tick_volume,
        real_volume=record.real_volume,
        spread=record.spread,
        provenance=provenance,
        quality=quality,
        quality_reason=quality_reason,
    )
