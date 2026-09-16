"""
Generate a reference swings dump from real, captured golden data --
Phase 11 task: "generate a Python reference-swings cross-check against
real golden data" (the immediate follow-up to VO_Swings.mq5).

PURPOSE. VO_Swings.mq5 (mql5/Indicators/VectorOdyssey/) has never been
compiled or run against a live MT5 terminal from this session -- see that
file's own header. Before anyone trusts what it draws, its output needs
something authoritative to check against. This script runs the REAL
Python SwingEngine (vo.observation.swings, the actual Phase 11 / gate G6
implementation, not a re-description of it) over the one corpus of real,
captured MT5 bars this project has (tests/fixtures/golden/
us100n_m1_bars_20260915.jsonl) and writes every swing it produces to a
plain JSONL file. Attach the indicator to US100.n on 1xTrade-Server with
that same history loaded, and its boxes should match this file's entries
one for one -- same pivot bar, same reversal, same CONFIRMED/BROKEN
status. A mismatch means the MQL5 port has a bug; VO_SwingMath.mqh's own
header names the one known, narrow, accepted divergence (MathRound vs.
Python round() tie-breaking in the ATR average) as the sole exception.

WHY THIS FILE DOES NOT LIVE IN tests/fixtures/golden/. That directory's
own README is explicit: "Files in this directory are captured verbatim
from a running MT5 terminal. Nothing here is hand-written, hand-corrected,
or reformatted. Ever." This script's OUTPUT is none of those things -- it
is DERIVED, by running real code over a real capture, but it is not itself
captured output. Mixing the two would quietly erode the one guarantee that
directory exists to keep. The derived reference lives in
tests/fixtures/derived/ instead, clearly labelled, and
tests/unit/test_swing_reference_golden.py pins it to a determinism check
(same discipline as test_golden_corpus.py's "never hand-edited" test, one
level removed: this file is never hand-edited from what THIS SCRIPT emits).

TIME RESOLUTION. The golden bars are schema v2: `timestamp` is broker
server wall-clock, not UTC (see vo.market.mapping.UnresolvedServerTimeError's
own docstring for why vo.market refuses to guess this). This script
resolves it the same way any real v2 consumer must: via
vo.time.brokers.resolve_broker_utc against the real, committed
config/settings/brokers.yaml profile for 1xTrade-Server -- not a
synthesized offset. The reference dump reports BOTH the resolved UTC
instant (what Bar.open_time_utc / SwingPoint.observed_at actually hold on
the Python side) and the raw broker-local wall-clock string exactly as
captured (what a person looking at the MT5 chart itself will see) -- the
second is what makes this dump usable for eyeballing against a live
terminal, since chart timestamps are broker-local, not UTC.

TIMEFRAME. BarRecordV2.timeframe in this capture is the literal string
"PERIOD_CURRENT" (see the golden README's provenance table) -- a known
bridge-capture quirk, not a real MT5 period spelling
(vo.market.timeframe.Timeframe.from_mt5 raises on it, correctly, since
"PERIOD_CURRENT" names no fixed timeframe by itself). This script does not
patch that by guessing; it labels every resolved Bar as M1 only after
INDEPENDENTLY VERIFYING the capture's own cadence -- asserting every
consecutive pair of resolved open_time_utc values is a whole, positive
number of 60-second steps apart (never zero/negative, never a fractional
minute) -- and raises loudly if that ever stops holding, rather than
silently mislabeling the data. A gap LARGER than one minute is expected
and left alone: this capture crosses exactly one server-day boundary
(seq 370, 2026-09-15T23:59:00 -> 2026-09-16T01:05:00, a 66-minute halt)
matching config/settings/brokers.yaml's own probe evidence for this
broker ("weekly open baseline 01:05 server time") -- a real daily
maintenance break in real market data, not a labeling error. What this
check actually guards against is the data secretly NOT being M1 at all
(e.g. true M5 bars would show a constant 300s spacing, never 60s).
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from vo.core.replay import ReplayHarness  # noqa: E402
from vo.market.bar import Bar  # noqa: E402
from vo.market.deserialization import json_to_record  # noqa: E402
from vo.market.mapping import resolve_instrument_id  # noqa: E402
from vo.market.records import BarRecordV2, SymbolRecordV2  # noqa: E402
from vo.market.sequence import BarSequence  # noqa: E402
from vo.market.timeframe import Timeframe  # noqa: E402
from vo.observation.swing_config import load_swing_config  # noqa: E402
from vo.observation.swings import SwingEngine, SwingLevel, SwingPoint  # noqa: E402
from vo.time.brokers import load_broker_profiles, resolve_broker_utc  # noqa: E402

BARS_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "us100n_m1_bars_20260915.jsonl"
META_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "us100n_meta_20260915.jsonl"
BROKERS_PATH = REPO_ROOT / "config" / "settings" / "brokers.yaml"
SWINGS_CONFIG_PATH = REPO_ROOT / "config" / "settings" / "swings.yaml"
OUTPUT_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "derived" / "us100n_m1_swings_reference_20260915.jsonl"
)

EXPECTED_BAR_SPACING = timedelta(minutes=1)


def _read_jsonl_records(path: Path) -> list:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            records.append(json_to_record(stripped))
    return records


def _load_tick_size() -> float:
    for record in _read_jsonl_records(META_PATH):
        if isinstance(record, SymbolRecordV2):
            return record.tick_size
    raise ValueError(f"{META_PATH}: no SymbolRecordV2 found -- cannot recover tick_size")


def _build_sequence() -> tuple[BarSequence, dict[str, str]]:
    """Resolves every captured bar to a real, UTC-timed Bar via the real
    broker profile, appends them in order into one BarSequence, and
    returns a bar_id -> raw broker-local timestamp string lookup (for
    labeling the reference dump with what a human sees on the chart)."""
    profiles = load_broker_profiles(BROKERS_PATH)
    profile = profiles.get("1xTrade-Server")
    if profile is None:
        raise ValueError(f"{BROKERS_PATH}: no profile for '1xTrade-Server'")

    records = [r for r in _read_jsonl_records(BARS_PATH) if isinstance(r, BarRecordV2)]
    records.sort(key=lambda r: r.seq)

    sequence = BarSequence()
    broker_local_by_bar_id: dict[str, str] = {}
    previous_utc = None

    for record in records:
        if record.timeframe != "PERIOD_CURRENT":
            raise ValueError(
                f"seq {record.seq}: expected the known 'PERIOD_CURRENT' capture "
                f"quirk this script accounts for, got {record.timeframe!r} instead "
                f"-- re-check the M1 cadence assumption before trusting this run"
            )

        resolution = resolve_broker_utc(record.timestamp, profile)
        instrument_id = resolve_instrument_id(record)

        if previous_utc is not None:
            spacing = resolution.utc - previous_utc
            spacing_seconds = spacing.total_seconds()
            if spacing_seconds <= 0 or spacing_seconds % EXPECTED_BAR_SPACING.total_seconds() != 0:
                raise ValueError(
                    f"seq {record.seq}: resolved bar spacing was {spacing} -- not a "
                    f"whole, positive multiple of {EXPECTED_BAR_SPACING} -- the capture "
                    f"is not the uniform M1 series this script assumes, so labeling "
                    f"every bar Timeframe.M1 would be a guess, not a verified fact "
                    f"(a gap LARGER than one minute, such as a daily maintenance "
                    f"break, is fine and expected -- see the module docstring)"
                )
        previous_utc = resolution.utc

        bar = Bar(
            instrument_id=instrument_id,
            timeframe=Timeframe.M1,
            open_time_utc=resolution.utc,
            open=record.open,
            high=record.high,
            low=record.low,
            close=record.close,
            tick_volume=record.tick_volume,
            real_volume=record.real_volume,
            spread=record.spread,
        )
        sequence = sequence.append(bar)
        broker_local_by_bar_id[bar.bar_id] = record.timestamp.isoformat()

    return sequence, broker_local_by_bar_id


def _swing_to_dict(swing: SwingPoint, broker_local_by_bar_id: dict[str, str]) -> dict:
    return {
        "level": swing.level.name,
        "swing_type": swing.swing_type.name,
        "status": swing.status.name,
        "object_id": swing.object_id,
        "supersedes": swing.supersedes,
        "pivot_bar_id": swing.pivot_bar_id,
        "pivot_broker_local_time": broker_local_by_bar_id.get(swing.pivot_bar_id),
        "price": swing.price,
        "confirmed_at_bar_id": swing.confirmed_at_bar_id,
        "confirmed_at_broker_local_time": broker_local_by_bar_id.get(swing.confirmed_at_bar_id),
        "reversal_ticks": swing.reversal_ticks,
        "atr_ticks_at_pivot": swing.atr_ticks_at_pivot,
        "reversal_extreme_price": swing.reversal_extreme_price,
        "reversal_extreme_bar_id": swing.reversal_extreme_bar_id,
        "reversal_extreme_broker_local_time": broker_local_by_bar_id.get(
            swing.reversal_extreme_bar_id
        )
        if swing.reversal_extreme_bar_id is not None
        else None,
    }


def generate_reference_lines() -> list[str]:
    """Runs both tiers' real SwingEngine over the real golden capture and
    returns one compact, deterministically-sorted JSON line per swing
    event (CONFIRMED and BROKEN both) -- the exact content this module
    writes to OUTPUT_PATH, importable directly so the pinning test never
    has to re-derive it by re-implementing this function."""
    sequence, broker_local_by_bar_id = _build_sequence()
    tick_size = _load_tick_size()
    swing_config = load_swing_config(SWINGS_CONFIG_PATH)

    all_swings: list[SwingPoint] = []
    for level in (SwingLevel.INTERNAL, SwingLevel.SWING):
        engine = SwingEngine.for_level(swing_config, level, tick_size=tick_size)
        for step in ReplayHarness(sequence).run(engine):
            all_swings.extend(step.result)

    rows = [_swing_to_dict(swing, broker_local_by_bar_id) for swing in all_swings]
    def _sort_key(row: dict) -> tuple:
        return (row["pivot_broker_local_time"], row["level"], row["swing_type"], row["status"])

    rows.sort(key=_sort_key)

    return [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]


def main() -> None:
    lines = generate_reference_lines()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} swing events to {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
