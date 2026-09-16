"""
Phase 12 live read probe -- connect to the running MT5 terminal and print
what the read adapter (vo.market.mt5) sees: account, one symbol, open
positions, pending orders.

    python scripts/mt5_read_probe.py [SYMBOL]

Windows only, and only useful with the MT5 terminal running and logged in
(the same machine the terminal is on). This is the human, live-terminal
verification for Phase 12 -- analogous to scripts/run_vo_ea.py for the
file-bridge pipeline. It sends no orders; it only reads.

If MetaTrader5 is not installed, the adapter raises a clear MT5Error
explaining the optional [mt5] install, rather than an opaque ImportError.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from vo.market.mt5 import MT5Error, MT5ReadClient


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "US100.n"
    client = MT5ReadClient()
    try:
        client.connect()
    except MT5Error as exc:
        print(f"Could not connect to the MT5 terminal: {exc}")
        return 1

    try:
        account = client.account()
        print("ACCOUNT")
        print(
            f"  login={account.login} server={account.server} "
            f"currency={account.currency} trade_allowed={account.trade_allowed}"
        )
        print(
            f"  balance={account.balance} equity={account.equity} "
            f"profit={account.profit} margin={account.margin} "
            f"free={account.margin_free} level={account.margin_level} "
            f"leverage={account.leverage}"
        )

        try:
            sym = client.symbol(symbol)
            print(f"SYMBOL {symbol}")
            print(
                f"  digits={sym.digits} point={sym.point} tick_size={sym.tick_size} "
                f"tick_value={sym.tick_value} contract_size={sym.contract_size}"
            )
        except MT5Error as exc:
            print(f"SYMBOL {symbol}: unavailable ({exc})")

        positions = client.positions()
        print(f"OPEN POSITIONS ({len(positions)})")
        for p in positions:
            print(
                f"  #{p.ticket} {p.broker_symbol} {p.side} vol={p.volume} "
                f"open={p.price_open} now={p.price_current} "
                f"sl={p.stop_loss} tp={p.take_profit} pnl={p.profit}"
            )

        orders = client.orders()
        print(f"PENDING ORDERS ({len(orders)})")
        for o in orders:
            print(
                f"  #{o.ticket} {o.broker_symbol} {o.kind} {o.state} "
                f"vol={o.volume_current} price={o.price_open} "
                f"sl={o.stop_loss} tp={o.take_profit}"
            )
    finally:
        client.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
