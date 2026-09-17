#!/usr/bin/env python3
"""
Publish a regime feed for VO_Regime.mq5 -- Phase 13a (Python computes,
the indicator reads a file).

    python scripts/publish_regime.py [config/settings/vo_ea.yaml] [--watch]

WHAT IT DOES. Reads the same wire files VO_Bridge.mq5 writes (the
<broker_symbol>_bars.jsonl / _meta.jsonl the running VO_EA already
tails), resolves every bar's broker-server wall-clock to UTC via the
real committed broker profile, runs the REAL vo.observation.regime
RegimeEngine over the resulting BarSequence, and writes the emitted
regime bands to <broker_symbol>_regime.feed in that same wire folder.
VO_Regime.mq5, attached to the chart, reads that .feed file and draws
the bands. No classifier logic lives in MQL5 (gate G6 discipline); this
is the one channel between them.

CONSISTENCY WITH THE LIVE EA. It sources the wire dir, broker_symbol,
and brokers.yaml path from the very same EAConfig run_vo_ea.py loads, so
"what the dashboard sees" and "what the chart draws" come from one
configuration. tick_size (needed by the SwingEngine the RegimeEngine
drives) is read from the _meta.jsonl the bridge emits, exactly as the
golden reference generator reads it from the captured meta.

TIME. RegimeState.observed_at is a resolved UTC instant; MT5 draws in
broker-server wall-clock. This script keeps each bar's ORIGINAL
broker-local timestamp from the wire (never a reverse DST computation)
and hands vo.telemetry.regime_feed a UTC->broker-epoch map built from
it, so the bands land on the right chart bars.

--watch re-publishes on an interval (the bridge appends live bars); the
default is a single pass. This is a viz-only feed (gate G2: never an
input to any decision path).
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from vo.core.config import EAConfig, load_ea_config  # noqa: E402
from vo.core.replay import ReplayHarness  # noqa: E402
from vo.market.bar import Bar  # noqa: E402
from vo.market.deserialization import json_to_record  # noqa: E402
from vo.market.mapping import resolve_instrument_id  # noqa: E402
from vo.market.records import BarRecordV2, SymbolRecordV2  # noqa: E402
from vo.market.sequence import BarSequence  # noqa: E402
from vo.market.timeframe import Timeframe  # noqa: E402
from vo.observation.regime_config import build_regime_engine, load_regime_config  # noqa: E402
from vo.observation.swing_config import load_swing_config  # noqa: E402
from vo.telemetry.regime_feed import build_regime_segments, render_feed_lines  # noqa: E402
from vo.time.brokers import BrokerProfile, load_broker_profiles, resolve_broker_utc  # noqa: E402

SWINGS_CONFIG_PATH = REPO_ROOT / "config" / "settings" / "swings.yaml"
REGIME_CONFIG_PATH = REPO_ROOT / "config" / "settings" / "regime.yaml"


def _read_jsonl_records(path: Path) -> list[object]:
    if not path.exists():
        return []
    records: list[object] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            try:
                records.append(json_to_record(stripped))
            except Exception:
                # A half-written live line (the bridge is appending); skip it.
                continue
    return records


def _load_tick_size(meta_path: Path) -> float:
    for record in _read_jsonl_records(meta_path):
        if isinstance(record, SymbolRecordV2):
            return record.tick_size
    raise ValueError(
        f"{meta_path}: no SymbolRecordV2 found -- cannot recover tick_size "
        f"(is the bridge writing {meta_path.name}?)"
    )


def _select_profile(
    profiles: dict[str, BrokerProfile], broker_server: str
) -> BrokerProfile:
    profile = profiles.get(broker_server)
    if profile is not None:
        return profile
    # Fall back only when the configuration is unambiguous.
    if len(profiles) == 1:
        return next(iter(profiles.values()))
    raise ValueError(
        f"no broker profile for server {broker_server!r} and {len(profiles)} "
        f"profiles configured -- add it to brokers.yaml so resolution is exact"
    )


def _build_sequence(
    bars_path: Path, profiles: dict[str, BrokerProfile]
) -> tuple[BarSequence, dict[datetime, int]]:
    """Resolve every captured bar to a UTC-timed Bar and return the
    sequence plus a UTC-instant -> broker-server-epoch map for drawing.

    The broker epoch is the raw broker wall-clock read as epoch seconds --
    exactly the value MT5's chart uses for that bar (server time), so no
    DST reversal is performed here."""
    records = [r for r in _read_jsonl_records(bars_path) if isinstance(r, BarRecordV2)]
    records.sort(key=lambda r: r.seq)

    sequence = BarSequence()
    broker_epoch_by_utc: dict[datetime, int] = {}

    for record in records:
        profile = _select_profile(profiles, resolve_instrument_id(record).broker_server)
        resolution = resolve_broker_utc(record.timestamp, profile)
        # Bridge-capture quirk: timeframe may be the literal "PERIOD_CURRENT".
        # These are M1 bars off an M1 chart; label them M1 (the reference
        # generator verifies 60s cadence -- live tailing can't, so we trust
        # the chart timeframe the bridge was attached to).
        timeframe = Timeframe.M1
        bar = Bar(
            instrument_id=resolve_instrument_id(record),
            timeframe=timeframe,
            open_time_utc=resolution.utc,
            open=record.open,
            high=record.high,
            low=record.low,
            close=record.close,
            tick_volume=record.tick_volume,
            real_volume=record.real_volume,
            spread=record.spread,
        )
        try:
            sequence = sequence.append(bar)
        except ValueError:
            # Out-of-order / duplicate bar -- a stale wire file with several
            # concatenated backfills. Skip it, exactly as ObservationPipeline
            # quarantines it, rather than crashing the publish.
            continue
        broker_epoch_by_utc[resolution.utc] = int(
            record.timestamp.replace(tzinfo=UTC).timestamp()
        )

    return sequence, broker_epoch_by_utc


def build_feed_lines(config: EAConfig) -> list[str]:
    """Run the real RegimeEngine over the wire bars and return the feed
    content (importable directly so tests never re-implement the wiring)."""
    profiles = load_broker_profiles(config.brokers_path)
    sequence, broker_epoch_by_utc = _build_sequence(config.bar_wire_path(), profiles)
    if not sequence.bars:
        return []

    tick_size = _load_tick_size(config.meta_wire_path())
    swing_config = load_swing_config(SWINGS_CONFIG_PATH)
    regime_config = load_regime_config(REGIME_CONFIG_PATH)

    engine = build_regime_engine(regime_config, swing_config, tick_size=tick_size)
    ReplayHarness(sequence).run(engine)

    segments = build_regime_segments(engine.states.all(), sequence.bars)

    def epoch_of(instant: datetime) -> int:
        epoch = broker_epoch_by_utc.get(instant)
        if epoch is None:
            raise KeyError(
                f"no broker epoch for {instant.isoformat()} -- segment boundary "
                f"is not a known bar time (should never happen)"
            )
        return epoch

    return render_feed_lines(
        segments, epoch_of=epoch_of, generated_utc=datetime.now(UTC)
    )


def _feed_path(config: EAConfig) -> Path:
    return config.wire.dir / f"{config.broker_symbol}_regime.feed"


def publish_once(config: EAConfig) -> int:
    lines = build_feed_lines(config)
    out_path = _feed_path(config)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic-ish replace: write a temp then rename, so VO_Regime.mq5 never
    # reads a half-written feed.
    tmp = out_path.with_suffix(".feed.tmp")
    tmp.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    tmp.replace(out_path)
    band_count = max(len(lines) - 1, 0)  # minus the header
    return band_count


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--watch"]
    watch = "--watch" in sys.argv
    config_path = argv[0] if argv else "config/settings/vo_ea.yaml"
    config = load_ea_config(config_path)

    if not watch:
        count = publish_once(config)
        print(f"wrote {count} regime bands to {_feed_path(config)}")
        return

    print(f"watching {config.bar_wire_path()} -- publishing regime feed (Ctrl+C to stop)")
    try:
        while True:
            count = publish_once(config)
            print(f"{datetime.now(UTC).isoformat()} wrote {count} regime bands")
            time.sleep(config.wire.poll_interval_seconds)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
