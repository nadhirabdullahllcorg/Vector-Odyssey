"""
Cross-restart persistence for ComplianceEngine -- the gap
vo.compliance.engine's own docstring flagged as "v2, flagged not built"
and the breach-closeout work (2026-09-19) made urgent: a closeout only
fires if a live snapshot actually reports BREACHED_*, so any state the
engine forgets on restart is a limit it stops enforcing.

WHAT IS PERSISTED, AND WHY EACH ONE MATTERS

  peak_equity      The trailing-drawdown reference. Without it, an EA
                   restarted after a drawdown re-anchors its peak to
                   whatever equity is current, silently GRANTING ITSELF
                   a fresh full drawdown allowance. This is the single
                   most dangerous thing to lose.

  total_breached   A total-drawdown breach is permanent by design
                   (see the engine's own comment). Forgetting it on
                   restart un-blocks an account the firm considers dead.

  trading_day +    The daily-loss anchor. PropFirmGuard re-anchors this
  day_start_equity on every restart, and the engine's docstring
                   previously described matching that. On reflection
                   that reference behavior is a real hazard, not a
                   convention worth copying: restart at -4% on the day
                   and a re-anchored day-start equity permits another
                   full daily allowance, breaching the real limit while
                   the engine reports SAFE. So the day anchor IS
                   persisted here, paired with the trading day it
                   belongs to -- restoring on the SAME trading day keeps
                   the original anchor, and a restart on a LATER day
                   re-anchors naturally through the engine's own
                   existing day-rollover branch. This is a deliberate
                   improvement on the reference implementation, recorded
                   here rather than silently diverging.

FAILING LOUD IS THE SAFE DIRECTION HERE -- a deliberate inversion of
this project's usual quarantine-not-abort discipline (E.7,
vo.market.ingestion / vo.market.economic_calendar_ingestion). A
malformed calendar line costs one skipped event; a malformed compliance
state file silently treated as "no state" costs the account its
drawdown memory, which is exactly the failure this module exists to
prevent. So a corrupt, truncated, version-mismatched, or WRONG-ACCOUNT
state file raises ComplianceStateError. Only a genuinely absent file is
non-fatal (first run on this account), and it returns None rather than
an empty state so the caller cannot confuse "never saved" with "saved
as zero".

ACCOUNT IDENTITY is part of the file and is checked on load. A prop
evaluation account and a live account are different accounts with
different peaks; restoring one onto the other would be both wrong and
invisible. The check is login + server, the same pair MT5's own
account_info() reports (vo.market.account.AccountState).

WRITES ARE ATOMIC (tempfile in the same directory, then os.replace).
A terminal killed mid-write must not leave a half-written state file
that then fails to load on the next start -- which, given the
fail-loud rule above, would block the EA rather than merely losing
data.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

CURRENT_STATE_VERSION = 1


class ComplianceStateError(ValueError):
    """Raised when a compliance state file cannot be trusted -- malformed,
    an unsupported version, or belonging to a different account. Never
    raised for a missing file (see load_compliance_state)."""


@dataclass(frozen=True, slots=True)
class CompliancePersistentState:
    """Everything a ComplianceEngine must remember across a restart. A
    plain frozen dataclass, same style as the rest of vo.compliance --
    not a CanonicalRecord: this is process state being checkpointed, not
    an observation VO made about the market."""

    version: int
    account_login: int
    account_server: str
    trading_day: date | None
    day_start_equity: float | None
    peak_equity: float | None
    total_breached: bool
    saved_at_utc: datetime

    def __post_init__(self) -> None:
        if self.saved_at_utc.tzinfo is None:
            raise ComplianceStateError("saved_at_utc must be tz-aware")
        if self.day_start_equity is not None and self.day_start_equity <= 0:
            raise ComplianceStateError("day_start_equity must be > 0 when set")
        if self.peak_equity is not None and self.peak_equity <= 0:
            raise ComplianceStateError("peak_equity must be > 0 when set")
        if (self.trading_day is None) != (self.day_start_equity is None):
            raise ComplianceStateError(
                "trading_day and day_start_equity must be set or unset together -- "
                "a day anchor without its day cannot be checked for staleness"
            )


def _to_json(state: CompliancePersistentState) -> str:
    return json.dumps(
        {
            "version": state.version,
            "account_login": state.account_login,
            "account_server": state.account_server,
            "trading_day": state.trading_day.isoformat() if state.trading_day else None,
            "day_start_equity": state.day_start_equity,
            "peak_equity": state.peak_equity,
            "total_breached": state.total_breached,
            "saved_at_utc": state.saved_at_utc.isoformat(),
        },
        indent=2,
        sort_keys=True,
    )


def save_compliance_state(path: str | Path, state: CompliancePersistentState) -> None:
    """Write the state file atomically. The temp file is created in the
    SAME directory as the target so os.replace stays a same-filesystem
    rename (atomic); a temp in /tmp could land on a different filesystem
    where replace is a copy and loses atomicity."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        dir=file_path.parent, prefix=f".{file_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_to_json(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, file_path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def load_compliance_state(
    path: str | Path,
    *,
    expected_login: int,
    expected_server: str,
) -> CompliancePersistentState | None:
    """Load and verify a saved state. Returns None ONLY when the file does
    not exist (first run on this account). Anything else that cannot be
    trusted raises ComplianceStateError -- see this module's own docstring
    on why this deliberately does not follow the quarantine convention."""
    file_path = Path(path)

    if not file_path.exists():
        return None

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComplianceStateError(f"{file_path}: unreadable compliance state -- {exc}") from exc

    if not isinstance(raw, dict):
        raise ComplianceStateError(f"{file_path}: compliance state must be a JSON object")

    version = raw.get("version")
    if version != CURRENT_STATE_VERSION:
        raise ComplianceStateError(
            f"{file_path}: unsupported compliance state version {version!r} "
            f"(this build writes and reads v{CURRENT_STATE_VERSION})"
        )

    try:
        login = int(raw["account_login"])
        server = str(raw["account_server"])
        raw_day = raw["trading_day"]
        trading_day = date.fromisoformat(raw_day) if raw_day is not None else None
        raw_day_equity = raw["day_start_equity"]
        day_start_equity = float(raw_day_equity) if raw_day_equity is not None else None
        raw_peak = raw["peak_equity"]
        peak_equity = float(raw_peak) if raw_peak is not None else None
        total_breached = bool(raw["total_breached"])
        saved_at_utc = datetime.fromisoformat(str(raw["saved_at_utc"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ComplianceStateError(f"{file_path}: malformed compliance state -- {exc}") from exc

    if saved_at_utc.tzinfo is None:
        saved_at_utc = saved_at_utc.replace(tzinfo=UTC)

    if login != expected_login or server != expected_server:
        raise ComplianceStateError(
            f"{file_path}: compliance state belongs to account {login}@{server}, "
            f"not {expected_login}@{expected_server} -- refusing to restore one "
            f"account's drawdown memory onto another"
        )

    return CompliancePersistentState(
        version=version,
        account_login=login,
        account_server=server,
        trading_day=trading_day,
        day_start_equity=day_start_equity,
        peak_equity=peak_equity,
        total_breached=total_breached,
        saved_at_utc=saved_at_utc,
    )
