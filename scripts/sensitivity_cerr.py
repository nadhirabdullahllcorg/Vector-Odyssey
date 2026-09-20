"""
CERR threshold sensitivity -- research only.

    MT5 history -> windows measured per length -> candidate grid screened
                -> causal CERR replay for usable candidates -> report

The question is whether a BROAD REGION of CERR definitions produces a
coherent, non-fragile population. Nothing here computes P&L, ranks a
candidate by outcome, or calls a region optimal. If the surface turns
out to be chaotic, that is a finding about CERR worth having before
anything else is built on it.

Two stages on purpose. Screening is arithmetic over already-measured
windows and costs nothing, so the whole grid is screened. Running the
causal replay is expensive, so only candidates whose population size
lands in a usable band go through -- and the band is about SIZE, never
about results.

Windows are re-measured at every length in --window-bars because the
first calibration run fixed the length at 20 and so cannot answer
whether a threshold encodes duration rather than structure.

Windows only: MetaTrader5 is a Windows package and the terminal must be
running. Output goes to reports/cerr/, which is gitignored.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Reuses the calibration script's loader so history is pulled exactly one
# way: the same measured broker profile, the same symbol metadata, the
# same quarantine handling. A second loader would eventually drift.
from calibrate_cerr import _load_bars  # noqa: E402

from vo.observation.swing_config import load_swing_config  # noqa: E402
from vo.observation.swings import SwingEngine  # noqa: E402
from vo.time.engine import VOTimeEngine  # noqa: E402
from vo.time.sessions import load_session_configs  # noqa: E402
from vo.valco.lrx_calibration import funnel, observe_cycles  # noqa: E402
from vo.valco.lrx_cerr import CerrConfig  # noqa: E402
from vo.valco.lrx_config import load_lrx_config  # noqa: E402
from vo.valco.lrx_consolidation import EfficiencyMeasure  # noqa: E402
from vo.valco.lrx_displacement import DisplacementConfig, QualificationMode  # noqa: E402
from vo.valco.lrx_mss import ConfirmationMethod, MssConfig  # noqa: E402
from vo.valco.lrx_replay import replay_config_from_lrx, replay_events  # noqa: E402
from vo.valco.lrx_sensitivity import (  # noqa: E402
    body_er_anomalies,
    build_grid,
    measure_windows,
    profile_window_length,
    screen_candidate,
    select_for_funnel,
)

SETTINGS = REPO_ROOT / "config" / "settings"
OUT_DIR = REPO_ROOT / "reports" / "cerr"


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


def _floats(text: str) -> list[float]:
    return [float(part) for part in text.split(",") if part.strip()]


def _ints(text: str) -> list[int]:
    return [int(part) for part in text.split(",") if part.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="US100")
    parser.add_argument("--bars", type=int, default=20_000)
    parser.add_argument("--window-bars", default="3,5,8,10,15,20")
    parser.add_argument("--range-atr", default="3,4,5,6,7")
    parser.add_argument("--net-move-atr", default="1.0,1.5,2.0,2.5,3.0")
    parser.add_argument("--efficiency", default="0.15,0.20,0.25,0.30,0.35,0.40")
    parser.add_argument("--minimum-bars", type=int, default=3)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument(
        "--funnel-max",
        type=int,
        default=24,
        help=(
            "How many usable candidates get the expensive causal replay. "
            "Selected by population size and spread across the grid, never "
            "by outcome."
        ),
    )
    parser.add_argument("--funnel-step", type=int, default=5)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    return parser.parse_args()


def _fmt(value: float | None, places: int = 4) -> str:
    return "—" if value is None else f"{value:.{places}f}"


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
        f"{bar_meta['first_bar_utc']} .. {bar_meta['last_bar_utc']}",
        flush=True,
    )

    time_engine = VOTimeEngine(load_session_configs(SETTINGS / "sessions.yaml"))
    swing_config = load_swing_config(SETTINGS / "swings.yaml")
    lrx = load_lrx_config(SETTINGS / "lrx.yaml")
    replay_config = replay_config_from_lrx(
        lrx,
        displacement=DisplacementConfig(mode=QualificationMode.ATR_RANGE),
        mss=MssConfig(method=ConfirmationMethod.CANDLE_CLOSE),
    )
    atr_period = replay_config.atr_period

    print("Replaying detectors once ...", flush=True)
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

    window_lengths = _ints(args.window_bars)
    per_length: dict[int, tuple] = {}
    profiles = []
    for length in window_lengths:
        print(f"Measuring windows of {length} bars ...", flush=True)
        measurements = measure_windows(
            bars,
            window_bars=length,
            minimum_bars=args.minimum_bars,
            atr_period=atr_period,
            tick_size=tick_size,
            step=args.step,
        )
        per_length[length] = measurements
        profiles.append(profile_window_length(measurements, window_bars=length))
        print(f"  {len(measurements)} windows", flush=True)

    grid = build_grid(
        window_bars=window_lengths,
        range_atr=_floats(args.range_atr),
        net_move_atr=_floats(args.net_move_atr),
        efficiency=_floats(args.efficiency),
        measures=[EfficiencyMeasure.KAUFMAN, EfficiencyMeasure.BODY],
    )
    print(f"Screening {len(grid)} candidate definitions ...", flush=True)
    screens = [
        screen_candidate(
            per_length[candidate.window_bars],
            candidate,
            minimum_bars=args.minimum_bars,
            atr_period=atr_period,
        )
        for candidate in grid
    ]

    chosen = select_for_funnel(screens, limit=args.funnel_max)
    print(
        f"Running the causal replay for {len(chosen)} usable candidates ...",
        flush=True,
    )
    funnels = []
    for position, screen in enumerate(chosen, start=1):
        config = screen.candidate.to_consolidation_config(
            minimum_bars=args.minimum_bars, atr_period=atr_period
        )
        cycles = observe_cycles(
            bars,
            consolidation_config=config,
            cerr_config=CerrConfig(),
            displacements=events.qualified_displacements,
            sweeps=events.sweeps,
            mss_events=events.mss_events,
            tick_size=tick_size,
            step=args.funnel_step,
        )
        counts = funnel(cycles)
        funnels.append((screen, counts))
        print(f"  [{position}/{len(chosen)}] {screen.candidate.label} -> {counts}")

    anomalies = body_er_anomalies(per_length[max(window_lengths)], limit=10)

    # ── report
    lines = [
        "# CERR threshold sensitivity",
        "",
        "Research only. No P&L, no ranking by outcome, no region called",
        "optimal. The question is whether a BROAD region of definitions",
        "produces a coherent, non-fragile population.",
        "",
        f"Source run: `{args.symbol}`, {bar_meta['bars_accepted']} bars, "
        f"`{bar_meta['first_bar_utc']}` .. `{bar_meta['last_bar_utc']}`, "
        f"git `{_git_short_hash()}`",
        "",
        f"Detector events (one replay, shared by all candidates): "
        f"`{events.counts}`",
        "",
        "## Window-length profiles",
        "",
        "Mandatory before any threshold is chosen: a limit like",
        "`range_atr < 5` can encode DURATION rather than structure, since",
        "longer windows mechanically span more range.",
        "",
        "| window | n | range_atr p25/med/p75 | net_move_atr "
        "| Kaufman ER | body ER | mean_range_atr | body ER>1 |",
        "|---:|---:|---|---|---|---|---|---:|",
    ]
    for profile in profiles:
        def trio(values) -> str:
            return " / ".join(_fmt(v, 3) for v in values)

        lines.append(
            f"| {profile.window_bars} | {profile.n} | "
            f"{trio(profile.range_atr)} | {trio(profile.net_move_atr)} | "
            f"{trio(profile.kaufman_efficiency_ratio)} | "
            f"{trio(profile.body_efficiency_ratio)} | "
            f"{trio(profile.mean_range_atr)} | {profile.body_er_above_one} |"
        )

    lines += [
        "",
        "## Candidate screen",
        "",
        "Population bands are about SIZE alone. `DEGENERATE` admits over",
        "90% and so defines nothing; `SPARSE` admits too little to study.",
        "",
        "| candidate | qualifying | % | population | med range_atr "
        "| med net_move_atr | med ER | med bars |",
        "|---|---:|---:|---|---|---|---|---|",
    ]
    for screen in screens:
        lines.append(
            f"| `{screen.candidate.label}` | {screen.qualifying} | "
            f"{screen.qualifying_pct:.2f} | {screen.population} | "
            f"{_fmt(screen.median_range_atr, 3)} | "
            f"{_fmt(screen.median_net_move_atr, 3)} | "
            f"{_fmt(screen.median_efficiency, 3)} | "
            f"{_fmt(screen.median_bar_count, 1)} |"
        )

    lines += ["", "## Downstream funnel (usable candidates)", ""]
    if not funnels:
        lines.append("No candidate produced a usable population.")
    else:
        lines += [
            "| candidate | cycles | expansion | retracement "
            "| reversal | final states |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for screen, counts in funnels:
            finals = ", ".join(
                f"{k.split(':', 1)[1]}={v}"
                for k, v in sorted(counts.items())
                if k.startswith("final:")
            )
            lines.append(
                f"| `{screen.candidate.label}` | {counts['cycles']} | "
                f"{counts['reached_expansion']} | "
                f"{counts['reached_retracement']} | "
                f"{counts['reached_reversal']} | {finals or '—'} |"
            )

    lines += [
        "",
        "## Body efficiency ratio above 1.0",
        "",
        "Not a bug in the arithmetic. The two ratios measure different",
        "paths:",
        "",
        "```",
        "kaufman = |close_n - close_0| / sum|close_i - close_i-1|",
        "body    = |close_n - open_0|  / sum|close_i - open_i|",
        "```",
        "",
        "Kaufman's denominator is the same path as its numerator, walked",
        "step by step, so it cannot exceed one. The body ratio's",
        "denominator sums only candle BODIES and never sees the gaps",
        "between one bar's close and the next bar's open. Where a window",
        "contains gaps the numerator crosses distance the denominator",
        "never counted.",
        "",
        "So it is body-only directional efficiency, not bounded path",
        "efficiency, and on a gapped instrument the two are not on a",
        "comparable scale. Recorded and documented, never clamped: a clamp",
        "would hide exactly the windows where the measures disagree most.",
        "",
    ]
    if anomalies:
        lines += [
            "| start | bars | net move | body path | body ER | Kaufman ER |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        lines += [
            f"| `{a.start_time}` | {a.bar_count} | {a.net_move_points:.2f} | "
            f"{a.total_path_points:.2f} | {a.body_efficiency_ratio:.3f} | "
            f"{_fmt(a.kaufman_efficiency_ratio, 3)} |"
            for a in anomalies
        ]
    else:
        lines.append("None in this sample.")

    (out_dir / "sensitivity.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    rows = [
        {
            "candidate": asdict(screen.candidate)
            | {"efficiency_measure": str(screen.candidate.efficiency_measure)},
            "screen": {
                "windows_measured": screen.windows_measured,
                "qualifying": screen.qualifying,
                "qualifying_pct": screen.qualifying_pct,
                "population": str(screen.population),
                "unmeasurable": screen.unmeasurable,
                "median_range_atr": screen.median_range_atr,
                "median_net_move_atr": screen.median_net_move_atr,
                "median_efficiency": screen.median_efficiency,
                "median_bar_count": screen.median_bar_count,
            },
            "source_run": {
                "symbol": bar_meta["symbol"],
                "first_bar_utc": bar_meta["first_bar_utc"],
                "last_bar_utc": bar_meta["last_bar_utc"],
                "bars_accepted": bar_meta["bars_accepted"],
                "git": _git_short_hash(),
            },
        }
        for screen in screens
    ]
    funnel_by_label = {s.candidate.label: c for s, c in funnels}
    for row in rows:
        label = (
            f"w{row['candidate']['window_bars']}"
            f"/r{row['candidate']['max_range_atr']:g}"
            f"/n{row['candidate']['max_net_move_atr']:g}"
            f"/e{row['candidate']['max_efficiency_ratio']:g}"
            f"/{row['candidate']['efficiency_measure']}"
        )
        row["funnel"] = funnel_by_label.get(label)

    with (out_dir / "sensitivity.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    (out_dir / "sensitivity_run.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "git": _git_short_hash(),
                "bars": bar_meta,
                "events": events.counts,
                "grid_size": len(grid),
                "funnel_ran": len(funnels),
                "window_lengths": window_lengths,
                "atr_period": atr_period,
                "displacement_search_bars": replay_config.return_max_bars,
                "funnel_step": args.funnel_step,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(
        f"\nWrote {len(rows)} screened candidates, {len(funnels)} funnels\n"
        f"  {out_dir / 'sensitivity.md'}\n"
        f"  {out_dir / 'sensitivity.jsonl'}\n"
        f"  {out_dir / 'sensitivity_run.json'}",
        flush=True,
    )


if __name__ == "__main__":
    main()
