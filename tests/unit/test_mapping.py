"""
vo.market.mapping — instrument identity + wire-record-to-domain mapping.

Closes E.5 (no instrument identity) and E.7 (validation-by-exception makes
a bad observation unrepresentable) from architecture/vo-architecture-audit.md,
scoped as narrowly as architecture/vo-candle-layer.md's Phase 4 row: the
mapper and the quarantine boundary, not the richer future Bar shape.
"""

from datetime import UTC, datetime

import pytest

from vo.market import (
    BarRecord,
    BarRecordV2,
    InstrumentId,
    MappingResult,
    SourceCapabilitiesRecord,
    SymbolRecord,
    SymbolRecordV2,
    TickRecord,
    TickRecordV2,
    UnresolvedServerTimeError,
    map_records,
    record_to_domain,
    resolve_instrument_id,
)
from vo.market.bar import Bar
from vo.market.identity import UNKNOWN_BROKER_SERVER
from vo.market.symbol import Symbol
from vo.market.tick import Tick

# ── InstrumentId itself ──────────────────────────────────────────────────


def test_instrument_id_key_is_stable_and_readable():
    ident = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")
    assert ident.key == "MT5:1xTrade-Server:US100.n"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"platform": "", "broker_server": "s", "broker_symbol": "sym"},
        {"platform": "MT5", "broker_server": "", "broker_symbol": "sym"},
        {"platform": "MT5", "broker_server": "s", "broker_symbol": ""},
    ],
)
def test_instrument_id_rejects_empty_parts(kwargs):
    with pytest.raises(ValueError):
        InstrumentId(**kwargs)


# ── resolve_instrument_id ────────────────────────────────────────────────


def test_resolve_instrument_id_from_v1_tick_source():
    record = TickRecord(
        timestamp_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        source="MT5:US100.n",
    )

    ident = resolve_instrument_id(record)

    assert ident.platform == "MT5"
    assert ident.broker_server == UNKNOWN_BROKER_SERVER
    assert ident.broker_symbol == "US100.n"


def test_resolve_instrument_id_from_v1_symbol_source_and_broker_symbol():
    record = SymbolRecord(
        broker_symbol="US100.n",
        description="E-mini Nasdaq 100/spot",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=1.0,
        source="MT5",
    )

    ident = resolve_instrument_id(record)

    assert ident.platform == "MT5"
    assert ident.broker_server == UNKNOWN_BROKER_SERVER
    assert ident.broker_symbol == "US100.n"


def test_resolve_instrument_id_from_v2_decomposed_fields():
    record = TickRecordV2(
        timestamp_server_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        volume_real=0.0,
        flags=0,
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )

    ident = resolve_instrument_id(record)

    assert ident == InstrumentId(
        platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
    )


def test_resolve_instrument_id_rejects_malformed_v1_source():
    record = TickRecord(
        timestamp_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        source="not-a-platform-symbol-pair",
    )

    with pytest.raises(ValueError):
        resolve_instrument_id(record)


def test_resolve_instrument_id_rejects_source_capabilities_partner_that_is_fine_actually():
    """
    SourceCapabilitiesRecord DOES carry decomposed identity fields, so
    identity resolution succeeds even though record_to_domain (below)
    refuses to map it to a domain object at all.
    """
    record = SourceCapabilitiesRecord(
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
        real_volume_available=False,
        tick_level_available=True,
    )

    ident = resolve_instrument_id(record)

    assert ident.broker_symbol == "US100.n"


# ── record_to_domain: v1 ─────────────────────────────────────────────────


def test_record_to_domain_maps_v1_bar():
    record = BarRecord(
        timestamp=datetime(2026, 9, 10, 18, 49, 0, tzinfo=UTC),
        open=29132.30,
        high=29159.39,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        timeframe="PERIOD_M1",
        source="MT5:US100.n",
    )

    result = record_to_domain(record)

    assert isinstance(result, Bar)
    assert result.open == 29132.30
    assert result.instrument_id == InstrumentId(
        platform="MT5", broker_server=UNKNOWN_BROKER_SERVER, broker_symbol="US100.n"
    )


def test_record_to_domain_maps_v1_tick_timestamp_from_utc_epoch_ms():
    record = TickRecord(
        timestamp_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        source="MT5:US100.n",
    )

    result = record_to_domain(record)

    assert isinstance(result, Tick)
    assert result.timestamp == datetime.fromtimestamp(1789025880000 / 1000, tz=UTC)
    assert result.timestamp.tzinfo is not None
    assert result.instrument_id is not None
    assert result.instrument_id.broker_symbol == "US100.n"


def test_record_to_domain_maps_v1_symbol():
    record = SymbolRecord(
        broker_symbol="US100.n",
        description="E-mini Nasdaq 100/spot",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=1.0,
        source="MT5",
    )

    result = record_to_domain(record)

    assert isinstance(result, Symbol)
    assert result.source == "MT5"
    assert result.instrument_id is not None
    assert result.instrument_id.platform == "MT5"


# ── record_to_domain: v2 ─────────────────────────────────────────────────


def test_record_to_domain_maps_v2_symbol():
    record = SymbolRecordV2(
        broker_symbol="US100.n",
        description="E-mini Nasdaq 100/spot",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=1.0,
        platform="MT5",
        broker_server="1xTrade-Server",
    )

    result = record_to_domain(record)

    assert isinstance(result, Symbol)
    assert result.source == "MT5"
    assert result.instrument_id == InstrumentId(
        platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
    )


def test_record_to_domain_refuses_v2_tick_server_wallclock_timestamp():
    record = TickRecordV2(
        timestamp_server_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        volume_real=0.0,
        flags=0,
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )

    with pytest.raises(UnresolvedServerTimeError):
        record_to_domain(record)


def test_record_to_domain_refuses_v2_bar_server_wallclock_timestamp():
    record = BarRecordV2(
        timestamp=datetime(2026, 9, 10, 18, 49, 0),
        open=29132.30,
        high=29159.39,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        spread=12,
        timeframe="PERIOD_M1",
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )

    with pytest.raises(UnresolvedServerTimeError):
        record_to_domain(record)


def test_record_to_domain_refuses_source_capabilities_record():
    record = SourceCapabilitiesRecord(
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
        real_volume_available=False,
        tick_level_available=True,
    )

    with pytest.raises(TypeError):
        record_to_domain(record)


def test_record_to_domain_does_not_weaken_domain_invariants():
    """A record that is wire-valid but domain-invalid (high < low) still
    raises exactly as constructing Bar directly would — the mapper is not
    a second, looser validation path."""
    record = BarRecord(
        timestamp=datetime(2026, 9, 10, 18, 49, 0, tzinfo=UTC),
        open=29132.30,
        high=29120.00,  # below low: invalid
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        timeframe="PERIOD_M1",
        source="MT5:US100.n",
    )

    with pytest.raises(ValueError):
        record_to_domain(record)


# ── map_records: batch quarantine (E.7) ──────────────────────────────────


def _good_bar() -> BarRecord:
    return BarRecord(
        timestamp=datetime(2026, 9, 10, 18, 49, 0, tzinfo=UTC),
        open=29132.30,
        high=29159.39,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        timeframe="PERIOD_M1",
        source="MT5:US100.n",
    )


def _invalid_bar() -> BarRecord:
    return BarRecord(
        timestamp=datetime(2026, 9, 10, 18, 50, 0, tzinfo=UTC),
        open=29132.30,
        high=29120.00,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        timeframe="PERIOD_M1",
        source="MT5:US100.n",
    )


def test_map_records_partitions_instead_of_aborting():
    records = [_good_bar(), _invalid_bar(), _good_bar()]

    result = map_records(records)

    assert isinstance(result, MappingResult)
    assert len(result.accepted) == 2
    assert all(isinstance(r, Bar) for r in result.accepted)
    assert len(result.quarantined) == 1
    assert result.quarantined[0].record is records[1]
    assert "high" in result.quarantined[0].reason.lower()
    assert result.all_accepted is False


def test_map_records_all_accepted_true_when_nothing_quarantined():
    result = map_records([_good_bar(), _good_bar()])

    assert result.all_accepted is True
    assert result.quarantined == ()


def test_map_records_quarantines_unresolved_v2_timestamps_too():
    v2_tick = TickRecordV2(
        timestamp_server_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        volume_real=0.0,
        flags=0,
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )

    result = map_records([_good_bar(), v2_tick])

    assert len(result.accepted) == 1
    assert len(result.quarantined) == 1
    assert "Time Engine" in result.quarantined[0].reason


def test_map_records_on_empty_input():
    result = map_records([])

    assert result.accepted == ()
    assert result.quarantined == ()
    assert result.all_accepted is True
