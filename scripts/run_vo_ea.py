#!/usr/bin/env python3
"""
Run one Vector Odyssey `VO_EA` v1 process (Phase 10).

    python scripts/run_vo_ea.py [config/settings/vo_ea.yaml]

One process: tails the wire files VO_Bridge.mq5 (or anything else
honoring the same wire format) appends to, runs the canonical -> candle
-> time -> levels pipeline, and publishes a RuntimeState over TCP for
the dashboard (scripts/run_dashboard.py) to pick up. No orders -- see
architecture/vo-phase-plan.md's Phase 10 ("VO_EA v1 -- observation").

Resolves SS7 (the MQL5-vs-Python-brain question the phase plan left open
before Phase 10): the brain runs here, in Python; VO_Bridge.mq5 stays a
thin bridge. Nothing in this process or vo.core/vo.telemetry imports
MetaTrader5 -- that stays exclusively Phase 12's, per gate G7.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys

from vo.core.config import load_ea_config
from vo.core.logging_setup import configure_logging
from vo.telemetry.ea_runtime import run_vo_ea


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/settings/vo_ea.yaml"
    config = load_ea_config(config_path)
    configure_logging(config.logging.path, config.logging.level)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_vo_ea(config))


if __name__ == "__main__":
    main()
