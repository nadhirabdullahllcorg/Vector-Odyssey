"""
End-to-end: read_jsonl (wire parsing, unchanged) feeding map_records
(domain mapping + quarantine, Phase 4 / E.7).

The middle record here is wire-valid — read_jsonl parses all three lines
without raising, exactly as before Phase 4 — but the third is
domain-invalid (bar high below its own low). That distinction is the
point: a malformed *wire* record still aborts read_jsonl outright (see
test_market_ingestion_invalid.py, unchanged); a wire-valid record that
violates a domain invariant is set aside by map_records with a reason,
instead of raising past the whole ingest.
"""

from pathlib import Path

from vo.market import Bar, Symbol, map_records
from vo.market.ingestion import read_jsonl


def test_read_jsonl_then_map_records_quarantines_the_domain_invalid_bar():
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "mt5_mixed_with_domain_invalid_bar.jsonl"
    )

    records = list(read_jsonl(fixture_path))
    assert len(records) == 3  # wire parsing accepted all three lines

    result = map_records(records)

    assert len(result.accepted) == 2
    assert isinstance(result.accepted[0], Symbol)
    assert isinstance(result.accepted[1], Bar)

    assert len(result.quarantined) == 1
    assert "low" in result.quarantined[0].reason.lower()
    assert result.all_accepted is False
