#!/usr/bin/env python3
"""
Backtest the regime over deep MT5 history -- Phase 13a follow-up.

    python scripts/backtest_regime.py [config/settings/vo_ea.yaml] [--bars N]

The live publisher (scripts/publish_regime.py) only ever sees the ~500
bars VO_Bridge backfills -- a short runway. This pulls a MUCH longer M1
history straight from the terminal (the Phase 12 read API's copy_rates,
the only MetaTrader5 caller, gate G7), runs the SAME real RegimeEngine
over it, and produces two things:

  1. the regime feed (<symbol>_regime.feed) covering the full history, so
     VO_Regime.mq5 draws deep bands -- a static snapshot, not the live
     --watch stream (stop --watch before running this, or they fight over
     the file);
  2. a markdown backtest report (regime_backtest_<symbol>.md) -- time in
     each regime, transition frequencies, ER/Hurst per regime, and the
     anticipation-lean accuracy (how often the [VO-H] lean matched the
     resolution) -- the "is this accurate?" question, quantified.

Deeper history also sharpens the LIVE regime: the engine anchors its
structure from more confirmed swings, so the classification at the right
edge is better-founded, not just longer. No new classification logic --
same engine, same gates (G2: the feed and report are viz/analysis, never
a decision path).
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from vo.core.config import load_ea_config  # noqa: E402
from vo.core.replay import ReplayHarness  # noqa: E402
from vo.market.bar import Bar  # noqa: E402
from vo.market.identity import InstrumentId  # noqa: E402
from vo.market.mt5 import MT5ReadClient  # noqa: E402
from vo.market.sequence import build_bar_sequence  # noqa: E402
from vo.market.timeframe import Timeframe  # noqa: E402
from vo.observation.regime_config import build_regime_engine, load_regime_config  # noqa: E402
from vo.observation.swing_config import load_swing_config  # noqa: E402
from vo.telemetry.regime_feed import (  # noqa: E402
    build_regime_markers,
    build_regime_segments,
    render_feed_lines,
)
from vo.telemetry.regime_report import build_regime_report, render_report_markdown  # noqa: E402
from vo.time.brokers import load_broker_profiles, resolve_broker_utc  # noqa: E402
from vo.time.sessions import load_session_configs  # noqa: E402

SWINGS_CONFIG_PATH = REPO_ROOT / "config" / "settings" / "swings.yaml"
REGIME_CONFIG_PATH = REPO_ROOT / "config" / "settings" / "regime.yaml"
DEFAULT_BAR_CAP = 100_000


def _parse_args(argv: list[str]) -> tuple[str, int]:
    config_path = "config/settings/vo_ea.yaml"
    bars = DEFAULT_BAR_CAP
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--bars" and i + 1 < len(argv):
            bars = int(argv[i + 1])
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if rest:
        config_path = rest[0]
    return config_path, bars


def _write_feed_atomic(out_path: Path, text: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".feed.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(10):
        try:
            tmp.replace(out_path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2)


def main() -> None:
    config_path, bar_cap = _parse_args(sys.argv[1:])
    config = load_ea_config(config_path)
    profiles = load_broker_profiles(config.brokers_path)
    swing_config = load_swing_config(SWINGS_CONFIG_PATH)
    regime_config = load_regime_config(REGIME_CONFIG_PATH)

    client = MT5ReadClient()
    client.connect()
    try:
        server = client.account().server
        symbol = client.symbol(config.broker_symbol)
        rates = client.copy_rates(config.broker_symbol, bar_cap)
    finally:
        client.shutdown()

    profile = profiles.get(server)
    if profile is None:
        if len(profiles) != 1:
            raise SystemExit(
                f"no broker profile for server {server!r} and {len(profiles)} "
                f"configured -- add it to brokers.yaml"
            )
        profile = next(iter(profiles.values()))

    session_configs = load_session_configs(config.sessions_path)
    session_config = session_configs.get(config.broker_symbol)

    instrument_id = InstrumentId(
        platform="MT5", broker_server=server, broker_symbol=config.broker_symbol
    )

    bars: list[Bar] = []
    broker_epoch_by_utc: dict[datetime, int] = {}
    for rate in rates:
        naive = datetime.fromtimestamp(rate.time_broker_epoch_s, tz=UTC).replace(tzinfo=None)
        resolution = resolve_broker_utc(naive, profile)
        bar = Bar(
            instrument_id=instrument_id,
            timeframe=Timeframe.M1,
            open_time_utc=resolution.utc,
            open=rate.open,
            high=rate.high,
            low=rate.low,
            close=rate.close,
            tick_volume=rate.tick_volume,
            real_volume=rate.real_volume,
            spread=rate.spread,
        )
        bars.append(bar)
        broker_epoch_by_utc[resolution.utc] = rate.time_broker_epoch_s

    # build_bar_sequence quarantines out-of-order/duplicate bars instead of
    # aborting (the same pipeline-level pattern -- see vo.market.sequence),
    # and does it in O(n): a hand-rolled .append() loop over 100k bars would
    # copy the whole accepted-so-far tuple on every bar (O(n^2) overall).
    build_result = build_bar_sequence(bars)
    sequence = build_result.sequence
    if build_result.quarantined:
        print(
            f"quarantined {len(build_result.quarantined)} out-of-order/duplicate "
            f"bar(s) out of {len(bars)} pulled -- kept the rest"
        )

    if not sequence.bars:
        raise SystemExit("no usable bars returned from the terminal")

    engine = build_regime_engine(regime_config, swing_config, tick_size=symbol.tick_size)
    ReplayHarness(sequence).run(engine)
    states = engine.states.all()
    transitions_log = engine.transitions.all()
    segments = build_regime_segments(states, sequence.bars)
    # RETRACEMENT/REVERSAL are momentary (see regime_feed's module docstring)
    # -- build_regime_segments correctly drops their zero-width runs, so
    # they get a point marker instead of a dropped band.
    markers = build_regime_markers(states, sequence.bars)

    # 1. Full-history feed for VO_Regime.mq5.
    def epoch_of(instant: datetime) -> int:
        return broker_epoch_by_utc[instant]

    feed_lines = render_feed_lines(
        segments, markers, epoch_of=epoch_of, generated_utc=datetime.now(UTC)
    )
    feed_path = config.wire.dir / f"{config.broker_symbol}_regime.feed"
    _write_feed_atomic(feed_path, ("\n".join(feed_lines) + "\n") if feed_lines else "")

    # 2. Backtest report.
    history_end = sequence.bars[-1].open_time_utc
    report = build_regime_report(
        segments,
        states,
        transitions_log=transitions_log,
        history_end_utc=history_end,
        bar_count=len(sequence),
        bars=sequence.bars,
        session_config=session_config,
    )
    markdown = render_report_markdown(report)
    report_path = REPO_ROOT / f"regime_backtest_{config.broker_symbol}.md"
    report_path.write_text(markdown, encoding="utf-8")

    print(f"bars used:        {len(sequence)} (of {len(rates)} pulled)")
    print(f"regime segments:  {len(segments)}")
    print(f"regime markers:   {len(markers)} (RETRACEMENT/REVERSAL resolution points)")
    if session_config is None:
        print(f"session breakdown: skipped (no session config for {config.broker_symbol!r})")
    print(f"feed written:     {feed_path}")
    print(f"report written:   {report_path}")
    print()
    print(markdown)


if __name__ == "__main__":
    main()
