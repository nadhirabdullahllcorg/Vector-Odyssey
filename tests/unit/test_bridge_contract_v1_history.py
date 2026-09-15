"""
The MQL5 → Python contract.

This is the test that was missing, and its absence is why 40 tests were green
over a path that had never carried a live message. Every fixture in
tests/fixtures/ was hand-written in correct ISO-8601; the bar bridge does not
emit correct ISO-8601.

The samples below are derived from the emitter source character by character —
see the reconstruction notes on each one. They are NOT captured output.
Captured output goes in tests/fixtures/golden/ and is consumed by
test_golden_corpus.py; see the README there. Derived samples prove the format
contract; captured samples prove the values and the broker's real quirks. Both
are needed, for different reasons.

Two defects are recorded here as strict xfails. Strict matters: when Phase 3
fixes the bridge, an xfail that starts passing FAILS the build, forcing the
marker to be removed rather than quietly outliving the bug.

PHASE 3 UPDATE: the bridge has been fixed (VO_Bridge.mq5, schema v2 — see
test_bridge_contract.py for the new contract). This file is now the
historical record, kept exactly as it was: proof of what the pre-Phase-3
bridges actually emitted, and why. Its xfails still describe VO_BarBridge.mq5
/ VO_SymbolBridge.mq5's D1/D2, which is accurate — those files are gone, but
BAR_AS_EMITTED and the D2 reconstruction below are frozen samples of what
they produced, not a live check of current bridge behavior, so nothing here
needs to change. Do not add new tests to this file; it is closed.
"""

from __future__ import annotations

import json

import pytest

from vo.market.deserialization import json_to_record
from vo.market.records import BarRecord, SymbolRecord, TickRecord
from vo.market.schema import (
    BAR_V1,
    SYMBOL_V1,
    TICK_V1,
    RecordSpec,
    needs_json_escaping,
    sample_wire_dict,
    validate_wire_dict,
)

# ── what the emitters actually produce ─────────────────────────────────────
#
# VO_BarBridge.mq5:
#     string timestamp = TimeToString(utc_bar_time, TIME_DATE | TIME_SECONDS);
#     ... "\"timestamp\":\"" + timestamp + "Z" + "\","
#
# MQL5 TimeToString with TIME_DATE|TIME_SECONDS returns "yyyy.mm.dd hh:mi:ss".
# Dots, and a space instead of a T. Appending "Z" does not make it ISO-8601.

BAR_AS_EMITTED = (
    '{"record_type":"bar",'
    '"timestamp":"2026.09.10 18:49:00Z",'
    '"open":29132.30,'
    '"high":29159.39,'
    '"low":29131.81,'
    '"close":29141.15,'
    '"tick_volume":231,'
    '"real_volume":0,'
    '"timeframe":"PERIOD_M1",'
    '"source":"MT5:US100.n"}'
)

# VO_TickBridge.mq5 — timestamp_ms is an integer, so no format hazard.
# DoubleToString(tick.volume, 8) yields "1.00000000", a valid JSON number.
TICK_AS_EMITTED = (
    '{"record_type":"tick",'
    '"timestamp_ms":1789025880000,'
    '"bid":29445.84,'
    '"ask":29446.61,'
    '"last":29445.84,'
    '"volume":1.00000000,'
    '"source":"MT5:US100.n"}'
)

# VO_SymbolBridge.mq5 — real broker output for US100.n, confirmed by the
# symbol bridge run recorded in the progress notes.
SYMBOL_AS_EMITTED = (
    '{"record_type":"symbol",'
    '"broker_symbol":"US100.n",'
    '"description":"E-mini Nasdaq 100/spot",'
    '"digits":2,'
    '"point":0.0100000000,'
    '"tick_size":0.0100000000,'
    '"tick_value":0.0100000000,'
    '"contract_size":1.0000000000,'
    '"source":"MT5"}'
)


# ── D1: the bar timestamp ──────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT D1: VO_BarBridge.mq5 builds its timestamp with TimeToString(), "
        "which emits 'yyyy.mm.dd hh:mi:ss'. The deserializer needs ISO-8601. "
        "Fixed in Phase 3 by formatting with StringFormat instead."
    ),
)
def test_bar_bridge_output_is_accepted_by_the_deserializer() -> None:
    record = json_to_record(BAR_AS_EMITTED)

    assert isinstance(record, BarRecord)
    assert record.close == 29141.15


def test_bar_bridge_output_is_at_least_valid_json() -> None:
    """The break is the timestamp format, not the JSON itself. Locate it precisely."""
    decoded = json.loads(BAR_AS_EMITTED)

    assert decoded["record_type"] == "bar"
    assert set(decoded) == set(BAR_V1.wire_keys)


def test_schema_identifies_the_bar_timestamp_as_the_single_defect() -> None:
    """
    Every other field the bar bridge emits is conformant. Naming the one
    broken field keeps Phase 3 narrow.
    """
    problems = validate_wire_dict(json.loads(BAR_AS_EMITTED))

    assert len(problems) == 1, f"expected exactly one problem, got {problems}"
    assert "timestamp" in problems[0]
    assert "ISO-8601" in problems[0]


# ── the two emitters that are fine ─────────────────────────────────────────


def test_tick_bridge_output_round_trips() -> None:
    record = json_to_record(TICK_AS_EMITTED)

    assert isinstance(record, TickRecord)
    assert record.timestamp_ms == 1789025880000
    assert record.bid == 29445.84
    assert not validate_wire_dict(json.loads(TICK_AS_EMITTED))


def test_symbol_bridge_output_round_trips() -> None:
    record = json_to_record(SYMBOL_AS_EMITTED)

    assert isinstance(record, SymbolRecord)
    assert record.broker_symbol == "US100.n"
    assert record.digits == 2
    assert not validate_wire_dict(json.loads(SYMBOL_AS_EMITTED))


# ── D2: unescaped string concatenation ─────────────────────────────────────
#
# VO_SymbolBridge.mq5 builds the record by concatenation:
#
#     "\"description\":\"" + SymbolInfoString(_Symbol, SYMBOL_DESCRIPTION) + "\","
#
# No escaping. US100.n's description happens to be quote-free, so the defect is
# invisible today and would surface on the first instrument whose description
# contains one.


def _symbol_record_as_the_bridge_would_build_it(description: str) -> str:
    """Reproduce the emitter's concatenation exactly, escaping included: none."""
    return (
        '{"record_type":"symbol",'
        '"broker_symbol":"TEST",'
        '"description":"' + description + '",'
        '"digits":2,'
        '"point":0.01,'
        '"tick_size":0.01,'
        '"tick_value":0.01,'
        '"contract_size":1.0,'
        '"source":"MT5"}'
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT D2: the MQL5 emitters concatenate strings into JSON without "
        "escaping, so a description containing a quote produces a malformed "
        "record. Fixed in Phase 3 by a shared VO_Json.mqh escaper."
    ),
)
def test_symbol_description_containing_a_quote_survives() -> None:
    payload = _symbol_record_as_the_bridge_would_build_it('Nasdaq "Mini" Index')

    record = json_to_record(payload)

    assert isinstance(record, SymbolRecord)
    assert record.description == 'Nasdaq "Mini" Index'


def test_a_quote_free_description_is_unaffected() -> None:
    """Scope the defect: only hazardous characters break it."""
    payload = _symbol_record_as_the_bridge_would_build_it("E-mini Nasdaq 100/spot")

    record = json_to_record(payload)

    assert isinstance(record, SymbolRecord)
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


# ── schema and deserializer must not drift apart again ─────────────────────


@pytest.mark.parametrize("spec", [TICK_V1, BAR_V1, SYMBOL_V1], ids=lambda s: s.record_type)
def test_deserializer_accepts_every_schema_conformant_record(spec: RecordSpec) -> None:
    record = json_to_record(json.dumps(sample_wire_dict(spec)))

    assert record is not None


@pytest.mark.parametrize("spec", [TICK_V1, BAR_V1, SYMBOL_V1], ids=lambda s: s.record_type)
def test_deserializer_requires_every_schema_field(spec: RecordSpec) -> None:
    """
    If the schema says a field is required, dropping it must be an error. This
    is what keeps the two definitions from drifting the way they already did
    once.
    """
    for name in spec.field_names:
        partial = sample_wire_dict(spec)
        del partial[name]

        with pytest.raises((TypeError, ValueError)):
            json_to_record(json.dumps(partial))
