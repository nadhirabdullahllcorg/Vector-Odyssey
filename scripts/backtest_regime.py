#!/usr/bin/env python3
"""
Backtest the regime over deep MT5 history -- Phase 13a follow-up.

    python scripts/backtest_regime.py [config/settings/vo_ea.yaml] [--bars N] \
        [--validate] [--hurst-report] [--er-report] [--markov-report]

--validate additionally builds a Regime Engine Validation Report v1
(vo.telemetry.regime_validation) from the SAME in-memory replay -- no
second MT5 pull, no second engine run -- and writes
regime_validation_<symbol>.md alongside the plain backtest report.

--hurst-report additionally builds Phase 15a's standalone Hurst
characterization report (vo.research.hurst_report) from the SAME
in-memory replay -- rolling distribution/window-length sensitivity,
by regime, preceding transitions, by session, out-of-sample -- and
writes hurst_report_<symbol>.md. Independent of --validate; either or
both may be passed. Samples on a stride (not every bar) since
hurst_exponent's cost grows with the window length -- see the module's
own docstring for why.

--er-report additionally builds Phase 15b's standalone Efficiency Ratio
characterization report (vo.research.efficiency_ratio_report) from the
SAME in-memory replay, same sections and sampling discipline as
--hurst-report -- and writes efficiency_ratio_report_<symbol>.md.
Independent of --validate and --hurst-report; any combination may be
passed.

--markov-report additionally builds Phase 17's standalone conditional
Markov transition study (vo.research.markov_report) from the SAME
in-memory replay -- baseline + Month 1 structural constraint check, by
session, by Hurst tercile, by Efficiency Ratio tercile, by preceding
pullback duration -- and writes markov_report_<symbol>.md. Reuses the
same rolling Hurst/ER series --hurst-report/--er-report build (a fresh
default-window-lengths sweep, independent of whether those flags are
also passed). Independent of the other flags; any combination may be
passed.

The live publisher (scripts/publish_regime.py) only ever sees the ~500
bars VO_Bridge backfills -- a short runway. This pulls a MUCH longer M1
history straight from the terminal (the Phase 12 read API's copy_rates,
the only MetaTrader5 caller, gate G7), runs the SAME real RegimeEngine
over it, and produces two things:

  1. the regime feed (<symbol>_regime.feed) covering the full history, so
     VO_ReferenceLevels.mq5 draws deep bands -- a static snapshot, not the live
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
from vo.research.efficiency_ratio_report import (  # noqa: E402
    DEFAULT_STRIDE as ER_DEFAULT_STRIDE,
)
from vo.research.efficiency_ratio_report import (  # noqa: E402
    DEFAULT_WINDOW_LENGTHS as ER_DEFAULT_WINDOW_LENGTHS,
)
from vo.research.efficiency_ratio_report import (  # noqa: E402
    build_er_by_regime_report,
    build_er_by_rth,
    build_er_by_session,
    build_rolling_er,
    build_transition_er_report,
    render_er_report_markdown,
)
from vo.research.hurst_report import (  # noqa: E402
    DEFAULT_STRIDE,
    DEFAULT_WINDOW_LENGTHS,
    build_hurst_by_regime_report,
    build_hurst_by_rth,
    build_hurst_by_session,
    build_rolling_hurst,
    build_transition_hurst_report,
    render_hurst_report_markdown,
)
from vo.research.markov_report import (  # noqa: E402
    build_preceding_pullback_duration_matrices,
    build_rth_matrices,
    build_session_matrices,
    build_value_bucket_matrices,
    render_markov_report_markdown,
)
from vo.research.regime_windows import RegimeInterval  # noqa: E402
from vo.research.statistics import (  # noqa: E402
    build_out_of_sample_report,
    build_window_distributions,
)
from vo.research.transitions import build_transition_matrix  # noqa: E402
from vo.telemetry.regime_feed import (  # noqa: E402
    build_regime_markers,
    build_regime_segments,
    build_session_boundaries,
    render_feed_lines,
)
from vo.telemetry.regime_report import (  # noqa: E402
    build_regime_report,
    durations_by_regime,
    render_report_markdown,
)
from vo.telemetry.regime_validation import (  # noqa: E402
    build_accuracy_validation,
    build_duration_distributions,
    build_evidence_comparison,
    build_period_breakdown,
    render_validation_report_markdown,
)
from vo.time.brokers import load_broker_profiles, resolve_broker_utc  # noqa: E402
from vo.time.sessions import load_session_configs  # noqa: E402

SWINGS_CONFIG_PATH = REPO_ROOT / "config" / "settings" / "swings.yaml"
REGIME_CONFIG_PATH = REPO_ROOT / "config" / "settings" / "regime.yaml"
# copy_rates_from_pos (vo.market.mt5.MT5ReadClient.copy_rates) returns
# whatever the terminal actually has cached, up to this count -- asking
# for more than exists is harmless (you get back what's available, not
# an error), so this is deliberately a high ceiling, not a target. If
# the terminal has less than this cached locally, scroll the chart back
# in MT5 (or use its History Center) to force it to download more before
# a bigger --bars actually returns more.
DEFAULT_BAR_CAP = 1_000_000


def _parse_args(argv: list[str]) -> tuple[str, int, bool, bool, bool, bool]:
    config_path = "config/settings/vo_ea.yaml"
    bars = DEFAULT_BAR_CAP
    validate = False
    hurst_report = False
    er_report = False
    markov_report = False
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--bars" and i + 1 < len(argv):
            bars = int(argv[i + 1])
            i += 2
        elif argv[i] == "--validate":
            validate = True
            i += 1
        elif argv[i] == "--hurst-report":
            hurst_report = True
            i += 1
        elif argv[i] == "--er-report":
            er_report = True
            i += 1
        elif argv[i] == "--markov-report":
            markov_report = True
            i += 1
        else:
            rest.append(argv[i])
            i += 1
    if rest:
        config_path = rest[0]
    return config_path, bars, validate, hurst_report, er_report, markov_report


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
    config_path, bar_cap, validate, hurst_report, er_report, markov_report = _parse_args(
        sys.argv[1:]
    )
    config = load_ea_config(config_path)
    profiles = load_broker_profiles(config.brokers_path)
    swing_config = load_swing_config(SWINGS_CONFIG_PATH)
    regime_config = load_regime_config(REGIME_CONFIG_PATH)

    print(
        f"requesting up to {bar_cap} bars for {config.broker_symbol!r} from MT5 "
        f"(cached bars return fast; anything the terminal has not already downloaded "
        f"from the broker is fetched now, over the network, and can take a while for "
        f"a deep request -- this is MT5's own history download, not this script)...",
        flush=True,
    )
    _t_pull = time.monotonic()
    client = MT5ReadClient()
    client.connect()
    try:
        server = client.account().server
        symbol = client.symbol(config.broker_symbol)
        rates = client.copy_rates(config.broker_symbol, bar_cap)
    finally:
        client.shutdown()
    print(f"  -> {len(rates)} bars pulled in {time.monotonic() - _t_pull:.1f}s", flush=True)

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
    print(f"replaying {len(sequence)} bars through the regime engine...", flush=True)
    _t_replay = time.monotonic()
    ReplayHarness(sequence).run(engine)
    print(f"  -> replay finished in {time.monotonic() - _t_replay:.1f}s", flush=True)
    states = engine.states.all()
    transitions_log = engine.transitions.all()
    segments = build_regime_segments(states, sequence.bars)
    # RETRACEMENT/REVERSAL are momentary (see regime_feed's module docstring)
    # -- build_regime_segments correctly drops their zero-width runs, so
    # they get a point marker instead of a dropped band.
    markers = build_regime_markers(states, sequence.bars)
    boundaries = (
        build_session_boundaries(sequence.bars, session_config)
        if session_config is not None
        else ()
    )

    print("building regime segments/markers/session boundaries and the report...", flush=True)
    _t_build = time.monotonic()

    # 1. Backtest report -- built first so its per-session breakdown
    # (report.per_session) can be reused as the feed's SSTAT lines below,
    # instead of calling build_session_breakdown a second time.
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

    # 2. Full-history feed for VO_ReferenceLevels.mq5 (regime drawing moved
    # there, v4) -- session_stats (SSTAT lines) reuse report.per_session so
    # the chart's summary panel and the markdown report always agree.
    def epoch_of(instant: datetime) -> int:
        return broker_epoch_by_utc[instant]

    feed_lines = render_feed_lines(
        segments,
        markers,
        boundaries,
        report.per_session,
        epoch_of=epoch_of,
        generated_utc=datetime.now(UTC),
    )
    feed_path = config.wire.dir / f"{config.broker_symbol}_regime.feed"
    feed_written = True
    try:
        _write_feed_atomic(feed_path, ("\n".join(feed_lines) + "\n") if feed_lines else "")
    except PermissionError as exc:
        # Non-fatal: something else has the live feed file open (a live MT5
        # chart with VO_ReferenceLevels.mq5 attached, or a still-running
        # `publish_regime.py --watch` -- see this script's own module
        # docstring). A multi-minute MT5 pull + replay is far too expensive
        # to throw away over a best-effort chart-refresh write failing; the
        # backtest/validation/Hurst reports below don't depend on this file
        # at all, so they still get built and written.
        feed_written = False
        print(
            f"  -> WARNING: could not write {feed_path} ({exc}). Something else has "
            f"it open -- a live MT5 chart with VO_ReferenceLevels.mq5 attached, or a "
            f"still-running `publish_regime.py --watch`, are the usual causes. "
            f"Continuing without updating the live feed; the reports below are "
            f"unaffected.",
            flush=True,
        )
    print(f"  -> feed/report built in {time.monotonic() - _t_build:.1f}s", flush=True)

    validation_report_path: Path | None = None
    if validate:
        print("building Regime Engine Validation Report v1...", flush=True)
        _t_validate = time.monotonic()
        periods = build_period_breakdown(segments, states, transitions_log, sequence.bars)
        transition_matrix = build_transition_matrix(transitions_log)
        durations = build_duration_distributions(
            durations_by_regime(segments, bars=sequence.bars)
        )
        evidence = build_evidence_comparison(states)
        accuracy = build_accuracy_validation(states, session_config=session_config)
        validation_markdown = render_validation_report_markdown(
            instrument_key=config.broker_symbol,
            timeframe_canonical="M1",
            generated_utc=datetime.now(UTC),
            periods=periods,
            transition_matrix=transition_matrix,
            durations=durations,
            evidence=evidence,
            accuracy=accuracy,
        )
        validation_report_path = REPO_ROOT / f"regime_validation_{config.broker_symbol}.md"
        validation_report_path.write_text(validation_markdown, encoding="utf-8")
        print(
            f"  -> validation report built in {time.monotonic() - _t_validate:.1f}s", flush=True
        )

    hurst_report_path: Path | None = None
    if hurst_report:
        print("building Hurst research report (Phase 15a)...", flush=True)
        _t_hurst = time.monotonic()
        # always sample the configured hurst_period too, even if it isn't
        # one of the default sweep values, so the "primary window" tables
        # are never silently empty
        hurst_window_lengths = tuple(
            sorted({*DEFAULT_WINDOW_LENGTHS, regime_config.hurst_period})
        )
        rolling = build_rolling_hurst(sequence.bars, window_lengths=hurst_window_lengths)
        window_dists = build_window_distributions(rolling, window_lengths=hurst_window_lengths)
        intervals = tuple(
            RegimeInterval(regime=seg.regime, start_utc=seg.start_utc, end_utc=seg.end_utc)
            for seg in segments
        )
        by_regime = build_hurst_by_regime_report(
            rolling,
            intervals,
            window_lengths=hurst_window_lengths,
            primary_window=regime_config.hurst_period,
        )
        transitions = build_transition_hurst_report(
            rolling.get(regime_config.hurst_period, []), transitions_log, max_gap_minutes=90.0
        )
        by_session = (
            build_hurst_by_session(rolling.get(regime_config.hurst_period, []), session_config)
            if session_config is not None
            else ()
        )
        by_rth = (
            build_hurst_by_rth(rolling.get(regime_config.hurst_period, []), session_config)
            if session_config is not None
            else ()
        )
        out_of_sample = build_out_of_sample_report(rolling, window_lengths=hurst_window_lengths)
        hurst_markdown = render_hurst_report_markdown(
            instrument_key=config.broker_symbol,
            timeframe_canonical="M1",
            generated_utc=datetime.now(UTC),
            primary_window=regime_config.hurst_period,
            stride=DEFAULT_STRIDE,
            window_distributions=window_dists,
            by_regime=by_regime,
            transitions=transitions,
            by_session=by_session,
            by_rth=by_rth,
            out_of_sample=out_of_sample,
        )
        hurst_report_path = REPO_ROOT / f"hurst_report_{config.broker_symbol}.md"
        hurst_report_path.write_text(hurst_markdown, encoding="utf-8")
        print(f"  -> Hurst report built in {time.monotonic() - _t_hurst:.1f}s", flush=True)

    er_report_path: Path | None = None
    if er_report:
        print("building Efficiency Ratio research report (Phase 15b)...", flush=True)
        _t_er = time.monotonic()
        # always sample the configured efficiency_ratio_period too, even if it
        # isn't one of the default sweep values, so the "primary window"
        # tables are never silently empty -- same pattern as --hurst-report.
        er_window_lengths = tuple(
            sorted({*ER_DEFAULT_WINDOW_LENGTHS, regime_config.efficiency_ratio_period})
        )
        er_rolling = build_rolling_er(sequence.bars, window_lengths=er_window_lengths)
        er_window_dists = build_window_distributions(er_rolling, window_lengths=er_window_lengths)
        er_intervals = tuple(
            RegimeInterval(regime=seg.regime, start_utc=seg.start_utc, end_utc=seg.end_utc)
            for seg in segments
        )
        er_by_regime = build_er_by_regime_report(
            er_rolling,
            er_intervals,
            window_lengths=er_window_lengths,
            primary_window=regime_config.efficiency_ratio_period,
        )
        er_transitions = build_transition_er_report(
            er_rolling.get(regime_config.efficiency_ratio_period, []),
            transitions_log,
            max_gap_minutes=90.0,
        )
        er_by_session = (
            build_er_by_session(
                er_rolling.get(regime_config.efficiency_ratio_period, []), session_config
            )
            if session_config is not None
            else ()
        )
        er_by_rth = (
            build_er_by_rth(
                er_rolling.get(regime_config.efficiency_ratio_period, []), session_config
            )
            if session_config is not None
            else ()
        )
        er_out_of_sample = build_out_of_sample_report(er_rolling, window_lengths=er_window_lengths)
        er_markdown = render_er_report_markdown(
            instrument_key=config.broker_symbol,
            timeframe_canonical="M1",
            generated_utc=datetime.now(UTC),
            primary_window=regime_config.efficiency_ratio_period,
            stride=ER_DEFAULT_STRIDE,
            window_distributions=er_window_dists,
            by_regime=er_by_regime,
            transitions=er_transitions,
            by_session=er_by_session,
            by_rth=er_by_rth,
            out_of_sample=er_out_of_sample,
        )
        er_report_path = REPO_ROOT / f"efficiency_ratio_report_{config.broker_symbol}.md"
        er_report_path.write_text(er_markdown, encoding="utf-8")
        print(f"  -> ER report built in {time.monotonic() - _t_er:.1f}s", flush=True)

    markov_report_path: Path | None = None
    if markov_report:
        print("building conditional Markov transition study (Phase 17)...", flush=True)
        _t_markov = time.monotonic()
        # a fresh default-window-lengths rolling sweep, independent of
        # whether --hurst-report/--er-report were also passed -- see the
        # module docstring's own note on this.
        markov_hurst_window = regime_config.hurst_period
        markov_er_window = regime_config.efficiency_ratio_period
        markov_hurst_rolling = build_rolling_hurst(
            sequence.bars, window_lengths=(markov_hurst_window,)
        )
        markov_er_rolling = build_rolling_er(sequence.bars, window_lengths=(markov_er_window,))

        markov_baseline = build_transition_matrix(transitions_log)
        markov_by_session = (
            build_session_matrices(transitions_log, session_config)
            if session_config is not None
            else {}
        )
        markov_by_rth = (
            build_rth_matrices(transitions_log, session_config)
            if session_config is not None
            else {}
        )
        markov_by_hurst, markov_hurst_cuts = build_value_bucket_matrices(
            transitions_log,
            markov_hurst_rolling.get(markov_hurst_window, []),
            max_gap_minutes=90.0,
        )
        markov_by_er, markov_er_cuts = build_value_bucket_matrices(
            transitions_log, markov_er_rolling.get(markov_er_window, []), max_gap_minutes=90.0
        )
        markov_by_duration, markov_duration_cuts = build_preceding_pullback_duration_matrices(
            transitions_log, states
        )
        markov_markdown = render_markov_report_markdown(
            instrument_key=config.broker_symbol,
            timeframe_canonical="M1",
            generated_utc=datetime.now(UTC),
            baseline=markov_baseline,
            by_session=markov_by_session,
            by_rth=markov_by_rth,
            by_hurst=markov_by_hurst,
            hurst_cuts=markov_hurst_cuts,
            hurst_window=markov_hurst_window,
            by_er=markov_by_er,
            er_cuts=markov_er_cuts,
            er_window=markov_er_window,
            by_pullback_duration=markov_by_duration,
            pullback_duration_cuts=markov_duration_cuts,
        )
        markov_report_path = REPO_ROOT / f"markov_report_{config.broker_symbol}.md"
        markov_report_path.write_text(markov_markdown, encoding="utf-8")
        print(f"  -> Markov report built in {time.monotonic() - _t_markov:.1f}s", flush=True)

    calendar_days = (history_end - sequence.bars[0].open_time_utc).total_seconds() / 86400.0
    trading_days = len(sequence) / 1440.0
    coverage_pct = (trading_days / calendar_days * 100.0) if calendar_days > 0 else 100.0
    print(f"bars used:        {len(sequence)} (of {len(rates)} pulled)")
    print(
        f"calendar coverage: {trading_days:.1f} trading days of "
        f"{calendar_days:.1f} calendar days ({coverage_pct:.1f}%) -- the rest is "
        f"weekends/closures with zero bars, now correctly excluded from durations below"
    )
    print(f"regime segments:  {len(segments)}")
    print(f"regime markers:   {len(markers)} (RETRACEMENT/REVERSAL resolution points)")
    if session_config is None:
        print(f"session breakdown: skipped (no session config for {config.broker_symbol!r})")
        print("session lines:    skipped (no session config)")
    else:
        print(f"session lines:    {len(boundaries)} (session-boundary vertical lines)")
    if feed_written:
        print(f"feed written:     {feed_path}")
    else:
        print(f"feed NOT written: {feed_path} (permission denied -- see warning above)")
    print(f"report written:   {report_path}")
    if validation_report_path is not None:
        print(f"validation report written: {validation_report_path}")
    if hurst_report_path is not None:
        print(f"Hurst report written: {hurst_report_path}")
    if er_report_path is not None:
        print(f"ER report written: {er_report_path}")
    if markov_report_path is not None:
        print(f"Markov report written: {markov_report_path}")
    print()
    print(markdown)


if __name__ == "__main__":
    main()
