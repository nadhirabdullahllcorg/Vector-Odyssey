"""
Building a LiveDispatcher from config -- the one place where all the
pieces that can place a real order are assembled.

THIS FUNCTION IS WHERE A PROCESS BECOMES ABLE TO TRADE. Everything it
wires together already exists and is tested; what this adds is the
assembly, and the assembly is what turns a research process into a
trading one. It is therefore deliberately explicit, deliberately opt-in,
and deliberately loud.

OPT-IN, NEVER INFERRED. build_live_dispatcher returns None unless
config/settings/vo_ea.yaml carries `live_trading.enabled: true`. There is
no clever fallback, no "enabled if a broker is reachable", no
enabled-by-presence-of-a-file. A process trades because someone wrote
`enabled: true` and meant it.

TWO CLIENTS, ONE TERMINAL. MT5's Python API holds a single
process-global connection, so MT5ReadClient (account, positions) and
MT5ExecutionClient (send_order) are two views of the same thing and
either may call connect(). That is why both are constructed here rather
than one being derived from the other.

THE COMPLIANCE PROFILE IS A SEPARATE PATH ON PURPOSE. A live personal
account and a prop evaluation have genuinely different rules
(compliance.yaml vs compliance_live.yaml -- see DailyLossMode), and
which one governs is a deployment decision, not something to guess from
the account number.

NEW YORK TIME IS RESOLVED HERE, ONCE. The compliance engine's trading-day
boundary and the strategy's session windows both key off NY time; a
process that computed it in two places could disagree with itself across
a DST transition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vo.compliance.compliance_config import load_compliance_config
from vo.compliance.engine import ComplianceEngine
from vo.compliance.news_gate import load_news_gate_config
from vo.compliance.state_store import load_compliance_state
from vo.core.config import EAConfig
from vo.core.mt5 import MT5ExecutionClient
from vo.execution.execution_config import load_execution_config
from vo.execution.preflight import PreflightSequence
from vo.execution.router import ExecutionRouter
from vo.market.mt5 import MT5ReadClient
from vo.telemetry.live_dispatch import LiveDispatcher

_NY = ZoneInfo("America/New_York")


def build_live_dispatcher(config: EAConfig) -> LiveDispatcher | None:
    """Assemble the live-order path, or return None if this process is
    not configured to trade.

    Raises rather than degrading if live trading IS enabled but something
    it needs is missing. A half-wired trading process is worse than one
    that refuses to start: the failure should happen at startup, in front
    of whoever turned it on, not silently at the first signal.
    """
    live = config.live_trading
    if live is None or not live.enabled:
        return None

    read_client = MT5ReadClient()
    read_client.connect()
    execution_client = MT5ExecutionClient()
    execution_client.connect()

    execution_config = load_execution_config(live.execution_config_path)
    compliance_config = load_compliance_config(live.compliance_config_path)
    news_gate_config = (
        load_news_gate_config(live.news_gate_config_path)
        if live.news_gate_config_path is not None
        else None
    )

    account = read_client.account()
    restored = (
        load_compliance_state(
            live.compliance_state_path,
            expected_login=account.login,
            expected_server=account.server,
        )
        if live.compliance_state_path is not None
        else None
    )

    compliance = ComplianceEngine(
        config=compliance_config,
        trading_day_opens=live.trading_day_opens,
        news_gate_config=news_gate_config,
        restored_state=restored,
    )

    def _now() -> datetime:
        return datetime.now(UTC)

    def _now_ny() -> datetime:
        return datetime.now(_NY).replace(tzinfo=None)

    preflight = PreflightSequence(
        client=execution_client,
        config=execution_config,
        positions_now=read_client.positions,
        now=_now,
    )

    return LiveDispatcher(
        router=ExecutionRouter(client=execution_client, config=execution_config),
        compliance=compliance,
        account_state=read_client.account,
        now=_now,
        now_ny=_now_ny,
        broker_symbol=config.broker_symbol,
        preflight=preflight,
        preflight_volume=live.preflight_volume,
        preflight_stop_distance=live.preflight_stop_distance,
        preflight_target_distance=live.preflight_target_distance,
        preflight_trail_improvement=live.preflight_trail_improvement,
    )


def describe_live_configuration(config: EAConfig) -> str:
    """One line for the startup log, so what a process is about to do is
    visible before it does it rather than inferred afterwards."""
    live = config.live_trading
    if live is None or not live.enabled:
        return "live trading DISABLED -- signals will be produced and logged, never sent"
    return (
        f"live trading ENABLED on {config.broker_symbol} "
        f"(compliance={Path(live.compliance_config_path).name}, "
        f"preflight volume={live.preflight_volume})"
    )
