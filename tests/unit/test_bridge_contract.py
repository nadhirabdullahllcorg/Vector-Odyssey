"""
The MQL5 -> Python contract, schema v2 — the merged VO_Bridge.mq5.

For the pre-Phase-3 contract (the three separate bridges, defects D1/D2 and
all), see test_bridge_contract_v1_history.py. That file is closed; this one
is what VO_Bridge.mq5 is held to going forward.

The samples below are derived from VO_Bridge.mq5 / VO_Records.mqh /
VO_Json.mqh character by character — see the reconstruction notes on each
one. They are NOT captured output: nobody has run VO_Bridge.mq5 in a real
terminal yet. Derived samples prove the format contract now, before that
capture happens; tests/unit/test_golden_corpus.py takes over proving real
broker values once tests/fixtures/golden/ has something in it.
"""

from __future__ import annotations

import json

import pytest

from vo.market.deserialization import json_to_record
from vo.market.records import (
    BarRecordV2,
    SourceCapabilitiesRecord,
    SymbolRecordV2,
    TickRecordV2,
)
from vo.market.schema import (
    BAR_V2,
    SOURCE_CAPABILITIES_V1,
    SYMBOL_V2,
    TICK_V2,
    RecordSpec,
    needs_json_escaping,
    sample_wire_dict,
    validate_wire_dict,
)

# ── what VO_Bridge.mq5 actually produces ────────────────────────────────
#
# VO_BuildBarRecord() in VO_Records.mqh:
#     "\"timestamp\":" + VO_JsonString(VO_IsoServerTime(server_time))
#
# VO_IsoServerTime() formats with StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",
# ...) — correct ISO shape (the D1 fix), no trailing Z (the F2 fix: this is a
# server wall-clock reading, not a claimed UTC instant).

BAR_AS_EMITTED = (
    '{"record_type":"bar",'
    '"schema_version":2,'
    '"timestamp":"2026-09-10T18:49:00",'
    '"open":29132.30,'
    '"high":29159.39,'
    '"low":29131.81,'
    '"close":29141.15,'
    '"tick_volume":231,'
    '"real_volume":0,'
    '"spread":12,'
    '"timeframe":"PERIOD_M1",'
    '"seq":4821,'
    '"source_feed":"live",'
    '"platform":"MT5",'
    '"broker_server":"1xTrade-Server",'
    '"broker_symbol":"US100.n"}'
)

# VO_BuildTickRecord() — timestamp_server_ms is MqlTick.time_msc verbatim,
# an integer, so no format hazard the way the bar timestamp had.
TICK_AS_EMITTED = (
    '{"record_type":"tick",'
    '"schema_version":2,'
    '"timestamp_server_ms":1789025880000,'
    '"bid":29445.84,'
    '"ask":29446.61,'
    '"last":null,'
    '"volume":1.00000000,'
    '"volume_real":0.00000000,'
    '"flags":6,'
    '"seq":90214,'
    '"source_feed":"live",'
    '"platform":"MT5",'
    '"broker_server":"1xTrade-Server",'
    '"broker_symbol":"US100.n"}'
)

# VO_BuildSymbolRecord() — description now passes through VO_JsonString(),
# which escapes before quoting (the D2 fix).
SYMBOL_AS_EMITTED = (
    '{"record_type":"symbol",'
    '"schema_version":2,'
    '"broker_symbol":"US100.n",'
    '"description":"E-mini Nasdaq 100/spot",'
    '"digits":2,'
    '"point":0.0100000000,'
    '"tick_size":0.0100000000,'
    '"tick_value":0.0100000000,'
    '"contract_size":1.0000000000,'
    '"platform":"MT5",'
    '"broker_server":"1xTrade-Server"}'
)

SOURCE_CAPABILITIES_AS_EMITTED = (
    '{"record_type":"source_capabilities",'
    '"schema_version":2,'
    '"platform":"MT5",'
    '"broker_server":"1xTrade-Server",'
    '"broker_symbol":"US100.n",'
    '"real_volume_available":false,'
    '"tick_level_available":true}'
)


# ── D1 + F2: the bar timestamp is fixed, and it no longer lies ────────────


def test_bar_bridge_output_is_accepted_by_the_deserializer() -> None:
    record = json_to_record(BAR_AS_EMITTED)

    assert isinstance(record, BarRecordV2)
    assert record.close == 29141.15
    # No UTC claim survives into the domain object either: naive, on purpose.
    assert record.timestamp.tzinfo is None


def test_schema_finds_no_problems_in_the_new_bar_output() -> None:
    problems = validate_wire_dict(json.loads(BAR_AS_EMITTED))

    assert problems == []


def test_a_v2_bar_timestamp_with_a_z_is_rejected() -> None:
    """
    A server timestamp that claims UTC by carrying a Z is not a formatting
    slip to wave through — it is exactly the F2 mistake schema v2 exists to
    catch. Reject it the same way D1's malformed dots were rejected in v1.
    """
    payload = BAR_AS_EMITTED.replace(
        '"timestamp":"2026-09-10T18:49:00"',
        '"timestamp":"2026-09-10T18:49:00Z"',
    )

    problems = validate_wire_dict(json.loads(payload))

    assert len(problems) == 1
    assert "timestamp" in problems[0]


# ── the rest of the v2 records ────────────────────────────────────────────


def test_tick_bridge_output_round_trips() -> None:
    record = json_to_record(TICK_AS_EMITTED)

    assert isinstance(record, TickRecordV2)
    assert record.timestamp_server_ms == 1789025880000
    assert record.bid == 29445.84
    assert record.source_feed == "live"
    assert not validate_wire_dict(json.loads(TICK_AS_EMITTED))


def test_symbol_bridge_output_round_trips() -> None:
    record = json_to_record(SYMBOL_AS_EMITTED)

    assert isinstance(record, SymbolRecordV2)
    assert record.broker_symbol == "US100.n"
    assert record.digits == 2
    assert not validate_wire_dict(json.loads(SYMBOL_AS_EMITTED))


def test_source_capabilities_round_trips() -> None:
    """
    New in v2: whether this broker provides real volume is recorded once,
    here, instead of being guessed from a per-bar zero (see schema.py).
    """
    record = json_to_record(SOURCE_CAPABILITIES_AS_EMITTED)

    assert isinstance(record, SourceCapabilitiesRecord)
    assert record.real_volume_available is False
    assert record.tick_level_available is True
    assert not validate_wire_dict(json.loads(SOURCE_CAPABILITIES_AS_EMITTED))


# ── D2: escaping now goes through VO_Json.mqh on every string field ──────


def _symbol_record_as_the_bridge_would_build_it(description: str) -> str:
    """
    Reproduce VO_BuildSymbolRecord()'s output exactly: VO_JsonString()
    escapes before quoting, so this reconstruction escapes too, unlike its
    v1-history counterpart which deliberately did not.
    """
    escaped = (
        description.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return (
        '{"record_type":"symbol",'
        '"schema_version":2,'
        '"broker_symbol":"TEST",'
        '"description":"' + escaped + '",'
        '"digits":2,'
        '"point":0.01,'
        '"tick_size":0.01,'
        '"tick_value":0.01,'
        '"contract_size":1.0,'
        '"platform":"MT5",'
        '"broker_server":"TEST-Server"}'
    )


def test_symbol_description_containing_a_quote_now_survives() -> None:
    payload = _symbol_record_as_the_bridge_would_build_it('Nasdaq "Mini" Index')

    record = json_to_record(payload)

    assert isinstance(record, SymbolRecordV2)
    assert record.description == 'Nasdaq "Mini" Index'


def test_a_quote_free_description_is_unaffected() -> None:
    payload = _symbol_record_as_the_bridge_would_build_it("E-mini Nasdaq 100/spot")

    record = json_to_record(payload)

    assert isinstance(record, SymbolRecordV2)
    assert record.description == "E-mini Nasdaq 100/spot"


@pytest.mark.parametrize(
    ("value", "hazardous"),
    [
        ("E-mini Nasdaq 100/spot", False),
        ("Brent Crude", False),
        ('Nasdaq "Mini"', True),
        ("back\\slash", True),
        ("line\nbreak", True),
    ],
)
def test_escaping_hazards_are_identified(value: str, hazardous: bool) -> None:
    assert needs_json_escaping(value) is hazardous


# ── schema and deserializer must not drift apart ──────────────────────────


@pytest.mark.parametrize(
    "spec", [TICK_V2, BAR_V2, SYMBOL_V2, SOURCE_CAPABILITIES_V1], ids=lambda s: s.record_type
)
def test_deserializer_accepts_every_v2_schema_conformant_record(spec: RecordSpec) -> None:
    record = json_to_record(json.dumps(sample_wire_dict(spec)))

    assert record is not None


@pytest.mark.parametrize(
    "spec", [TICK_V2, BAR_V2, SYMBOL_V2, SOURCE_CAPABILITIES_V1], ids=lambda s: s.record_type
)
def test_deserializer_requires_every_v2_schema_field(spec: RecordSpec) -> None:
    for name in spec.field_names:
        if name == "schema_version":
            continue  # read via .get() with a default; absence means "v1", not an error

        partial = sample_wire_dict(spec)
        del partial[name]

        with pytest.raises((TypeError, ValueError)):
            json_to_record(json.dumps(partial))


def test_a_record_with_no_schema_version_is_treated_as_v1() -> None:
    """
    Backward compatibility is deliberate, not accidental: anything written
    before v2 existed — hand fixtures, a v1 golden capture — has no
    schema_version field at all, and must keep parsing exactly as it always
    did rather than being rejected or silently reinterpreted as v2 shaped.
    """
    from vo.market.records import BarRecord

    v1_style = json.dumps(
        {
            "record_type": "bar",
            "timestamp": "2026-09-10T18:49:00Z",
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "tick_volume": 10,
            "real_volume": 0,
            "timeframe": "M1",
            "source": "MT5:TEST",
        }
    )

    record = json_to_record(v1_style)

    assert isinstance(record, BarRecord)


def test_an_unknown_schema_version_is_rejected_not_guessed() -> None:
    payload = json.dumps({**sample_wire_dict(BAR_V2), "schema_version": 3})

    with pytest.raises(ValueError, match="schema_version"):
        json_to_record(payload)
