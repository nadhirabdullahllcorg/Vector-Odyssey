"""
Pins tests/fixtures/derived/us100n_m1_swings_reference_20260915.jsonl to
what scripts/generate_swing_reference.py actually produces right now.

Same discipline as test_golden_corpus.py's "never hand-edited" guard, one
level removed: that test protects the CAPTURED corpus from being touched
by hand; this one protects the DERIVED reference from silently drifting
out of sync with the real Python SwingEngine it is supposed to speak for.
If this test fails after a real, intended change to the Swing Engine or
its config, the fix is to re-run `python scripts/generate_swing_reference.py`
and commit the new output -- never to hand-edit the fixture to match.
"""

from __future__ import annotations

from pathlib import Path

from scripts.generate_swing_reference import OUTPUT_PATH, generate_reference_lines


def test_reference_file_matches_a_fresh_run() -> None:
    fresh_lines = generate_reference_lines()
    committed_lines = Path(OUTPUT_PATH).read_text(encoding="utf-8").splitlines()

    assert fresh_lines == committed_lines, (
        "tests/fixtures/derived/us100n_m1_swings_reference_20260915.jsonl is out "
        "of sync with what scripts/generate_swing_reference.py produces right "
        "now -- re-run that script and commit its output, do not hand-edit the "
        "fixture"
    )


def test_reference_file_is_never_hand_edited() -> None:
    """Same weak-but-useful guard as test_golden_corpus.py's own check:
    generate_reference_lines() emits compact JSON with no spaces after
    separators, so a reformatted line means someone opened this fixture
    in an editor rather than regenerating it."""
    lines = Path(OUTPUT_PATH).read_text(encoding="utf-8").splitlines()

    suspicious = [
        f"line {i}" for i, line in enumerate(lines, start=1) if '": ' in line or '", "' in line
    ]

    assert not suspicious, (
        "these lines look reformatted rather than generated:\n  "
        + "\n  ".join(suspicious)
    )


def test_reference_file_has_both_confirmed_and_broken_events() -> None:
    """A sanity floor, not a precise count (the exact figures belong to
    the generated file itself, not duplicated here as a magic number):
    the real golden capture is long enough that both lifecycle states
    and both tiers should appear at least once, or something upstream is
    badly wrong."""
    lines = Path(OUTPUT_PATH).read_text(encoding="utf-8").splitlines()

    assert len(lines) > 0, "no swing events were generated from the golden capture"
    assert any('"status":"CONFIRMED"' in line for line in lines)
    assert any('"status":"BROKEN"' in line for line in lines)
    assert any('"level":"INTERNAL"' in line for line in lines)
    assert any('"level":"SWING"' in line for line in lines)
