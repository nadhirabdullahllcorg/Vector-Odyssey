"""
Pull real MT5 history and produce the CERR calibration dataset.

    MT5 terminal -> M1 bars -> lrx_replay -> sweeps/displacements/MSS
                                          -> lrx_calibration -> datasets

WHAT THIS IS FOR. CERR's thresholds are all UNCONFIGURED, deliberately:
nothing has been guessed. This script produces the distributions from
which they can be CHOSEN. It is descriptive only -- it computes no P&L,
ranks nothing, and selects no threshold. A script that picked whichever
number produced the prettiest history would be an overfitting machine
wearing a research coat.

TIME COMES FROM MEASURED CONFIG, NEVER FROM THIS MACHINE. MT5 rates
carry broker-server epochs. They are resolved through
vo.time.brokers.resolve_broker_utc against brokers.yaml, whose offset
was measured by a real VO_BrokerTimeProbe run rather than assumed, and
session structure comes from sessions.yaml (US100: America/New_York,
RTH 09:30-16:00, settlement 16:14, trading day opening 18:00). The
operator's own wall clock is never consulted. Getting this wrong would
not fail loudly -- it would relocate every reference level in the
dataset by a fixed offset and produce a plausible, wrong study.

MULTI-DAY BY NECESSITY, NOT PREFERENCE. Previous-day levels, the
opening range and settlement all need a prior session. A single day
yields only RTH_SETTLEMENT, leaving nothing to raid, and the funnel
would be empty for reasons that have nothing to do with the strategy.
The default pull is deliberately large enough to span several sessions.

THE SEARCH HORIZON IS INFRASTRUCTURE, NOT A STRATEGY PARAMETER.
`displacement_search_bars` decides which bars the replay ASKS the
detectors about; it is not a claim that expansion should occur within N
bars. It is recorded in the run's metadata so its influence on the
counts stays visible and can be sensitivity-tested later. Tuning it to
manufacture more attractive MSS sequences would be exactly the failure
this separation exists to prevent.

Windows only: MetaTrader5 is a Windows package and the terminal must be
running and logged in. Output goes under reports/cerr/, which is
generated and regenerable.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from vo.market.bar import Bar  # noqa: E402
from vo.market.identity import InstrumentId  # noqa: E402
from vo.market.mt5 import MT5ReadClient  # noqa: E402
from vo.market.sequence import build_bar_sequence  # noqa: E402
from vo.market.timeframe import Timeframe  # noqa: E402
from vo.observation.swing_config import load_swing_config  # noqa: E402
from vo.observation.swings import SwingEngine  # noqa: E402
from vo.time.brokers import load_broker_profiles, resolve_broker_utc  # noqa: E402
from vo.time.engine import VOTimeEngine  # noqa: E402
from vo.time.sessions import load_session_configs  # noqa: E402
from vo.valco.lrx_calibration import (  # noqa: E402
    describe,
    funnel,
    observe_consolidations,
    observe_cycles,
    write_jsonl,
)
from vo.valco.lrx_cerr import CerrConfig  # noqa: E402
from vo.valco.lrx_config import load_lrx_config  # noqa: E402
from vo.valco.lrx_consolidation import ConsolidationConfig  # noqa: E402
from vo.valco.lrx_displacement import DisplacementConfig, QualificationMode  # noqa: E402
from vo.valco.lrx_mss import ConfirmationMethod, MssConfig  # noqa: E402
from vo.valco.lrx_replay import replay_config_from_lrx, replay_events  # noqa: E402

SETTINGS = REPO_ROOT / "config" / "settings"
OUT_DIR = REPO_ROOT / "reports" / "cerr"

DISTRIBUTION_FIELDS = (
    "range_points",
    "range_atr",
    "net_move_points",
    "net_move_atr",
    "total_path_points",
    "mean_range_points",
    "mean_range_atr",
    "kaufman_efficiency_ratio",
    "body_efficiency_ratio",
)


def _git_short_hash() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="US100")
    parser.add_argument(
        "--bars",
        type=int,
        default=20_000,
        help=(
            "M1 bars to pull. 20000 is roughly two trading weeks, enough for "
            "previous-day levels and several regimes. A single session cannot "
            "produce the level set the funnel needs."
        ),
    )
    parser.add_argument("--window-bars", type=int, default=20)
    parser.add_argument("--minimum-bars", type=int, default=10)
    parser.add_argument(
        "--step",
        type=int,
        default=1,
        help="Thin the consolidation sweep for very long histories.",
    )
    parser.add_argument(
        "--displacement-search-bars",
        type=int,
        default=0,
        help=(
            "Replay search horizon; 0 uses the sweep's own return_max_bars. "
            "Infrastructure, not a strategy parameter -- recorded in metadata."
        ),
    )
    parser.add_argument(
        "--displacement-mode",
        default=QualificationMode.ATR_RANGE.value,
        choices=[m.value for m in QualificationMode],
    )
    parser.add_argument(
        "--mss-method",
        default=ConfirmationMethod.CANDLE_CLOSE.value,
        choices=[m.value for m in ConfirmationMethod],
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    return parser.parse_args()


def _load_bars(symbol: str, count: int) -> tuple[list[Bar], float, dict[str, object]]:
    """Pull rates and resolve every broker epoch through the MEASURED
    profile. Quarantined bars are reported, never silently dropped.

    Tick size comes from the terminal's own symbol info: every
    ATR-relative measurement in the dataset is denominated in it, and a
    guessed value would rescale the entire study silently.
    """
    profiles = load_broker_profiles(SETTINGS / "brokers.yaml")
    client = MT5ReadClient()
    client.connect()
    try:
        account = client.account()
        symbol_info = client.symbol(symbol)
        rates = client.copy_rates(symbol, count)
    finally:
        client.shutdown()

    profile = profiles[account.server]
    instrument = InstrumentId(
        platform="MT5", broker_server=account.server, broker_symbol=symbol
    )

    bars: list[Bar] = []
    for rate in rates:
        naive = datetime.fromtimestamp(rate.time_broker_epoch_s, tz=UTC).replace(
            tzinfo=None
        )
        resolution = resolve_broker_utc(naive, profile)
        bars.append(
            Bar(
                instrument_id=instrument,
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
        )

    sequenced = build_bar_sequence(bars)
    meta: dict[str, object] = {
        "broker_server": account.server,
        "symbol": symbol,
        "rates_requested": count,
        "rates_returned": len(rates),
        "bars_accepted": len(sequenced.sequence),
        "bars_quarantined": len(sequenced.quarantined),
        "tick_size": symbol_info.tick_size,
        "point": symbol_info.point,
        "tick_value": symbol_info.tick_value,
        "contract_size": symbol_info.contract_size,
        "digits": symbol_info.digits,
        "broker_standard_offset_hours": profile.standard_utc_offset_hours,
        "broker_dst_offset_hours": profile.dst_utc_offset_hours,
        "broker_dst_calendar": str(profile.dst_calendar),
        "broker_confidence": str(profile.confidence),
        "first_bar_utc": bars[0].open_time_utc.isoformat() if bars else None,
        "last_bar_utc": bars[-1].open_time_utc.isoformat() if bars else None,
    }
    return list(sequenced.sequence.bars), symbol_info.tick_size, meta


def _data_quality(
    bars,
    rows,
    cycles,
    events,
    *,
    bar_meta: dict[str, object],
    time_engine: VOTimeEngine,
    horizon: int,
) -> str:
    """A reproducibility record, written BEFORE any interpretation.

    The distinction this section exists to make: DATA availability is not
    EVENT availability. A low MSS count can mean the market produced few
    qualifying structures -- or that history was short, ATR never warmed
    up, or the search horizon discarded most candidates. Those are
    different findings and the numbers below separate them.
    """
    instrument = bars[0].instrument_id
    trading_days = sorted(
        {
            time_engine.context_for(bar.open_time_utc, instrument).trading_day
            for bar in bars
        }
    )
    rth_sessions = sorted(
        {
            time_engine.context_for(bar.open_time_utc, instrument).trading_day
            for bar in bars
            if str(
                time_engine.context_for(bar.open_time_utc, instrument).session
            ).startswith("NY")
        }
    )

    # Gap size is measured in the series' OWN timeframe, not in minutes.
    # Hardcoding one minute reported every interval of an M15 series as a
    # gap -- the metric has to know what a normal step is before it can
    # say what an abnormal one is.
    step_seconds = bars[0].timeframe.seconds
    gaps = 0
    missing_bars = 0
    if step_seconds:
        for earlier, later in pairwise(bars):
            elapsed = (later.open_time_utc - earlier.open_time_utc).total_seconds()
            if elapsed > step_seconds:
                gaps += 1
                missing_bars += int(elapsed // step_seconds) - 1

    zero_range = sum(1 for bar in bars if bar.high <= bar.low)
    atr_unavailable = sum(1 for row in rows if row.range_atr is None)
    body_er_undefined = sum(1 for row in rows if row.body_efficiency_ratio is None)

    lines = [
        "## Data quality",
        "",
        "Recorded before interpretation. **Data availability is not event",
        "availability**: a low MSS count can mean the market produced few",
        "qualifying structures, or that history was short, ATR never warmed up,",
        "or the search horizon discarded candidates. These numbers separate",
        "those explanations.",
        "",
        "```",
        f"Requested M1 bars      : {bar_meta['rates_requested']}",
        f"Retrieved M1 bars      : {bar_meta['rates_returned']}",
        f"Accepted into sequence : {bar_meta['bars_accepted']}",
        f"Quarantined (dupe/order): {bar_meta['bars_quarantined']}",
        f"Actual UTC start       : {bar_meta['first_bar_utc']}",
        f"Actual UTC end         : {bar_meta['last_bar_utc']}",
        f"Trading days covered   : {len(trading_days)}",
        f"Days with NY session   : {len(rth_sessions)}",
        f"Timeframe              : {bars[0].timeframe}",
        f"Gaps beyond one bar    : {gaps}",
        f"Bars absent in gaps    : {missing_bars}",
        "  (weekend and session closures appear here and are expected)",
        f"Zero-range bars        : {zero_range}",
        "",
        f"Consolidation windows  : {len(rows)}",
        f"  ATR unavailable      : {atr_unavailable}",
        f"  body ER undefined    : {body_er_undefined}",
        f"Levels observed        : {events.counts['levels_seen']}",
        f"Swings confirmed       : {events.counts['swings']}",
        f"Sweeps                 : {events.counts['sweeps']}",
        f"Displacements          : {events.counts['displacements']}",
        f"MSS                    : {events.counts['mss']}",
        f"CERR cycles            : {len(cycles)}",
        "",
        f"Symbol                 : {bar_meta['symbol']}",
        f"Tick size              : {bar_meta['tick_size']}",
        f"Point                  : {bar_meta['point']}",
        f"Tick value             : {bar_meta['tick_value']}",
        f"Contract size          : {bar_meta['contract_size']}",
        f"Digits                 : {bar_meta['digits']}",
        "",
        f"Broker server          : {bar_meta['broker_server']}",
        f"Broker std offset (h)  : {bar_meta['broker_standard_offset_hours']}",
        f"Broker DST offset (h)  : {bar_meta['broker_dst_offset_hours']}",
        f"Broker DST calendar    : {bar_meta['broker_dst_calendar']}",
        f"Broker confidence      : {bar_meta['broker_confidence']}",
        "Session profile        : sessions.yaml (America/New_York, RTH 09:30-16:00,",
        "                         settlement 16:14, trading day opens 18:00)",
        f"Displacement horizon   : {horizon} bars (infrastructure, not strategy)",
        "```",
        "",
    ]
    if trading_days:
        lines[-1:] = [
            f"Trading days: `{trading_days[0]}` .. `{trading_days[-1]}`",
            "",
        ]
    return "\n".join(lines)


def _render_distributions(rows, cycles) -> str:
    lines = [
        "# CERR calibration — distributions",
        "",
        "Descriptive only. No threshold is proposed here; these are the",
        "measurements from which thresholds can be chosen.",
        "",
        f"Consolidation windows measured: **{len(rows)}**",
        "",
        "| field | n | missing | min | p25 | median | p75 | max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def fmt(value: float | None) -> str:
        return "—" if value is None else f"{value:.4f}"

    for entry in describe(rows, DISTRIBUTION_FIELDS):
        lines.append(
            f"| `{entry.field}` | {entry.count} | {entry.missing} | "
            f"{fmt(entry.minimum)} | {fmt(entry.p25)} | {fmt(entry.median)} | "
            f"{fmt(entry.p75)} | {fmt(entry.maximum)} |"
        )

    lines += ["", "## Cycle funnel", ""]
    if not cycles:
        lines += [
            "No cycles. With consolidation thresholds UNCONFIGURED this is the",
            "expected and correct result: no window is classified, so no cycle",
            "can begin. Choose candidate limits from the table above and pass",
            "them to a second run.",
        ]
    else:
        counts = funnel(cycles)
        lines += ["| stage | count |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    return "\n".join(lines) + "\n"


def main() -> None:
    args = _parse_args()
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Pulling {args.bars} M1 bars for {args.symbol} ...", flush=True)
    bars, tick_size, bar_meta = _load_bars(args.symbol, args.bars)
    if not bars:
        raise SystemExit("no bars returned; is the terminal logged in?")
    print(
        f"  {bar_meta['bars_accepted']} accepted, "
        f"{bar_meta['bars_quarantined']} quarantined, "
        f"{bar_meta['first_bar_utc']} .. {bar_meta['last_bar_utc']}",
        flush=True,
    )

    time_engine = VOTimeEngine(load_session_configs(SETTINGS / "sessions.yaml"))
    swing_config = load_swing_config(SETTINGS / "swings.yaml")
    lrx = load_lrx_config(SETTINGS / "lrx.yaml")

    replay_config = replay_config_from_lrx(
        lrx,
        displacement=DisplacementConfig(
            mode=QualificationMode(args.displacement_mode)
        ),
        mss=MssConfig(method=ConfirmationMethod(args.mss_method)),
    )
    if args.displacement_search_bars:
        import dataclasses

        replay_config = dataclasses.replace(
            replay_config, displacement_search_bars=args.displacement_search_bars
        )

    horizon = args.displacement_search_bars or replay_config.return_max_bars

    print("Replaying detectors ...", flush=True)
    events = replay_events(
        bars,
        time_engine=time_engine,
        swing_engine_factory=lambda level: SwingEngine.for_level(
            swing_config, level, tick_size=tick_size
        ),
        config=replay_config,
        tick_size=tick_size,
    )
    print(f"  {events.counts}", flush=True)

    # UNCONFIGURED on purpose: measurements only, nothing classified.
    consolidation_config = ConsolidationConfig(
        window_bars=args.window_bars, minimum_bars=args.minimum_bars
    )
    print("Measuring consolidation windows ...", flush=True)
    rows = observe_consolidations(
        bars, consolidation_config, tick_size=tick_size, step=args.step
    )

    cycles = observe_cycles(
        bars,
        consolidation_config=consolidation_config,
        cerr_config=CerrConfig(),
        displacements=events.displacements,
        sweeps=events.sweeps,
        mss_events=events.mss_events,
        tick_size=tick_size,
        step=args.step,
    )

    written_rows = write_jsonl(rows, out_dir / "consolidations.jsonl")
    written_cycles = write_jsonl(cycles, out_dir / "cycles.jsonl")

    metadata = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git": _git_short_hash(),
        "bars": bar_meta,
        "events": events.counts,
        "consolidation_config": {
            "window_bars": args.window_bars,
            "minimum_bars": args.minimum_bars,
            "thresholds": "UNCONFIGURED",
        },
        "replay_infrastructure": {
            "displacement_search_bars": horizon,
            "source": (
                "explicit"
                if args.displacement_search_bars
                else "defaulted from sweep.return_max_bars"
            ),
            "note": (
                "Search horizon, not a strategy parameter: it decides which "
                "bars the detectors are ASKED about. Recorded so its effect "
                "on the counts stays visible and can be sensitivity-tested."
            ),
        },
        "detector_definitions": {
            "displacement_mode": args.displacement_mode,
            "mss_confirmation": args.mss_method,
            "sweep_min_penetration_atr": replay_config.min_penetration_atr,
            "sweep_return_max_bars": replay_config.return_max_bars,
            "atr_period": replay_config.atr_period,
        },
        "rows": {"consolidations": written_rows, "cycles": written_cycles},
    }
    (out_dir / "run.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )
    report = "\n".join(
        [
            _render_distributions(rows, cycles),
            "",
            _data_quality(
                bars,
                rows,
                cycles,
                events,
                bar_meta=bar_meta,
                time_engine=time_engine,
                horizon=horizon,
            ),
        ]
    )
    (out_dir / "distributions.md").write_text(report, encoding="utf-8")

    print(
        f"\nWrote {written_rows} consolidation rows and {written_cycles} cycle rows\n"
        f"  {out_dir / 'consolidations.jsonl'}\n"
        f"  {out_dir / 'cycles.jsonl'}\n"
        f"  {out_dir / 'distributions.md'}\n"
        f"  {out_dir / 'run.json'}",
        flush=True,
    )


if __name__ == "__main__":
    main()
