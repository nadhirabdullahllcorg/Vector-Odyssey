"""
Shared regime-interval type and membership lookup -- vo.research (layer 5).

WHY THIS EXISTS. Phase 15a's hurst_report.py needed "which regime
interval covers this timestamp" to group rolling samples by regime; the
lookup (bisect over interval starts, same open/half-open-end contract as
vo.telemetry.regime_feed.RegimeSegment) has nothing Hurst-specific about
it, and Phase 15b's efficiency_ratio_report.py needs the exact same
thing. Following this project's own duplication convention (see
vo.research.statistics's module docstring for the fuller statement of
it): a tiny one-off helper may be duplicated across layers to dodge an
import cycle, but a real, non-trivial piece of logic used by more than
one module gets ONE shared home instead -- this is that home, extracted
out of hurst_report.py rather than copy-pasted a second time.

LAYERING. vo.research (layer 5) may not import vo.telemetry (layer 7) --
see tests/unit/test_architecture.py's LAYERS dict -- so RegimeInterval is
a plain, decoupled (regime, start_utc, end_utc) span, not a
vo.telemetry.regime_feed.RegimeSegment. Callers (script/orchestrator
layers, outside vo's own layering) adapt their already-built
RegimeSegments into these at the call site, e.g.:

    intervals = tuple(
        RegimeInterval(regime=seg.regime, start_utc=seg.start_utc, end_utc=seg.end_utc)
        for seg in segments
    )

This is a thin, one-line adapter, not a duplicated computation: no
segment-building/merging logic is reimplemented here or in either report
module.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.observation.regime import RegimeType


@dataclass(frozen=True)
class RegimeInterval:
    """A plain (regime, start_utc, end_utc) span. end_utc is None for the
    final, still-open interval (mirrors RegimeSegment's own convention)."""

    regime: RegimeType
    start_utc: datetime
    end_utc: datetime | None


def interval_at(
    intervals: Sequence[RegimeInterval], starts: Sequence[datetime], when: datetime
) -> RegimeInterval | None:
    """Which interval (if any) covers `when`, assuming `intervals` is
    chronologically ordered and contiguous (the same contract regime_feed.
    build_regime_segments already guarantees its own output). `starts` is
    intervals' own start_utc values, precomputed once by the caller so a
    whole report doesn't re-derive it per lookup."""
    if not intervals:
        return None
    idx = bisect.bisect_right(starts, when) - 1
    if idx < 0:
        return None
    interval = intervals[idx]
    if interval.end_utc is not None and when >= interval.end_utc:
        return None
    return interval
