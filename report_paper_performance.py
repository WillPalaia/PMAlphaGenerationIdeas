"""Comprehensive strategy attribution and paper performance report.

Inspects SQLite capture databases and outputs strategy-level P&L, fill rates,
equity curves, drawdowns, and market exposure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def analyze_database(db_path: str | Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. Snapshot coverage
    snapshot_count = conn.execute("SELECT COUNT(1) FROM market_snapshots").fetchone()[0]
    quoted_count = conn.execute(
        "SELECT COUNT(1) FROM market_snapshots WHERE yes_bid IS NOT NULL OR yes_ask IS NOT NULL"
    ).fetchone()[0]
    markets_count = conn.execute("SELECT COUNT(DISTINCT market_id) FROM market_snapshots").fetchone()[0]
    resolved_count = conn.execute("SELECT COUNT(1) FROM market_snapshots WHERE resolved = 1").fetchone()[0]

    # 2. Portfolio equity and drawdowns
    equity_rows = conn.execute(
        "SELECT timestamp_ms, cash, positions_value, equity, fees FROM paper_equity ORDER BY id"
    ).fetchall()

    starting_equity = float(equity_rows[0]["equity"]) if equity_rows else 100.0
    latest_equity = float(equity_rows[-1]["equity"]) if equity_rows else starting_equity
    latest_cash = float(equity_rows[-1]["cash"]) if equity_rows else starting_equity
    latest_positions_val = float(equity_rows[-1]["positions_value"]) if equity_rows else 0.0
    total_fees = float(equity_rows[-1]["fees"]) if equity_rows else 0.0

    peak = starting_equity
    max_drawdown_dollars = 0.0
    max_drawdown_pct = 0.0
    for row in equity_rows:
        eq = float(row["equity"])
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0
        if dd > max_drawdown_dollars:
            max_drawdown_dollars = dd
        if dd_pct > max_drawdown_pct:
            max_drawdown_pct = dd_pct

    # 3. Latest market quotes for MTM valuation
    latest_quotes = {}
    for r in conn.execute("""
        SELECT market_id, yes_bid, yes_ask, resolved, settlement_yes
        FROM market_snapshots
        WHERE id IN (SELECT MAX(id) FROM market_snapshots GROUP BY market_id)
    """):
        latest_quotes[r["market_id"]] = {
            "bid": r["yes_bid"],
            "ask": r["yes_ask"],
            "resolved": bool(r["resolved"]),
            "settlement_yes": r["settlement_yes"],
        }

    # 4. Strategy performance breakdown
    order_cols = [c["name"] for c in conn.execute("PRAGMA table_info(paper_orders)").fetchall()]
    has_strat_col = "strategy" in order_cols
    strat_field = "COALESCE(NULLIF(strategy, ''), signal)" if has_strat_col else "signal"

    order_rows = conn.execute(
        f"""
        SELECT {strat_field} as strategy_name,
               status,
               reject_reason,
               side,
               COUNT(1) as cnt,
               SUM(quantity) as requested_qty,
               SUM(filled_quantity) as filled_qty
        FROM paper_orders
        GROUP BY {strat_field}, status, reject_reason, side
        """
    ).fetchall()

    strategies: dict[str, dict] = defaultdict(
        lambda: {
            "orders": 0,
            "fills": 0,
            "rejections": 0,
            "reject_reasons": defaultdict(int),
            "buy_fills": 0,
            "sell_fills": 0,
            "buy_qty": 0.0,
            "sell_qty": 0.0,
            "buy_vol": 0.0,
            "sell_vol": 0.0,
            "fees_paid": 0.0,
            "fill_rate": 0.0,
            "positions": defaultdict(lambda: {"qty": 0.0, "cost_basis": 0.0, "realized_pnl": 0.0}),
        }
    )

    for r in order_rows:
        name = r["strategy_name"] or "unknown"
        s = strategies[name]
        s["orders"] += r["cnt"]
        if r["status"] == "filled":
            s["fills"] += r["cnt"]
        else:
            s["rejections"] += r["cnt"]
            reason = r["reject_reason"] or "unknown"
            s["reject_reasons"][reason] += r["cnt"]

    # Fills and executions
    fills = conn.execute(
        f"""
        SELECT f.id, f.order_id, f.timestamp_ms, f.venue, f.market_id, f.side, f.quantity, f.price, f.fee,
               {strat_field} as strat
        FROM paper_fills f
        JOIN paper_orders o ON f.order_id = o.client_order_id
        ORDER BY f.id ASC
        """
    ).fetchall()

    for f in fills:
        strat = f["strat"] or "unknown"
        s = strategies[strat]
        fee = float(f["fee"])
        qty = float(f["quantity"])
        px = float(f["price"])
        mkt = f["market_id"]
        pos = s["positions"][mkt]
        s["fees_paid"] += fee

        if f["side"] == "buy":
            s["buy_fills"] += 1
            s["buy_qty"] += qty
            s["buy_vol"] += qty * px
            pos["cost_basis"] += qty * px
            pos["qty"] += qty
        elif f["side"] == "sell":
            s["sell_fills"] += 1
            s["sell_qty"] += qty
            s["sell_vol"] += qty * px
            if pos["qty"] > 0:
                avg_c = pos["cost_basis"] / pos["qty"]
                closed_qty = min(qty, pos["qty"])
                pos["realized_pnl"] += closed_qty * (px - avg_c) - fee
                pos["qty"] -= closed_qty
                pos["cost_basis"] = pos["qty"] * avg_c

    # Calculate MTM, Realized P&L, Unrealized P&L for each strategy
    strategy_report = []
    for strat_name, s in strategies.items():
        s["fill_rate"] = (s["fills"] / s["orders"] * 100.0) if s["orders"] > 0 else 0.0
        realized_pnl = sum(p["realized_pnl"] for p in s["positions"].values())
        unrealized_pnl = 0.0
        mtm_position_val = 0.0
        total_cost_basis = 0.0
        active_positions_cnt = 0

        for mkt, pos in s["positions"].items():
            if pos["qty"] > 1e-6:
                active_positions_cnt += 1
                total_cost_basis += pos["cost_basis"]
                quote = latest_quotes.get(mkt)
                if quote:
                    if quote["resolved"]:
                        mark_px = 1.0 if quote["settlement_yes"] else 0.0
                    elif quote["bid"] is not None and quote["ask"] is not None:
                        mark_px = (quote["bid"] + quote["ask"]) / 2.0
                    elif quote["bid"] is not None:
                        mark_px = quote["bid"]
                    elif quote["ask"] is not None:
                        mark_px = quote["ask"]
                    else:
                        mark_px = pos["cost_basis"] / pos["qty"]
                else:
                    mark_px = pos["cost_basis"] / pos["qty"]

                cur_val = pos["qty"] * mark_px
                mtm_position_val += cur_val
                unrealized_pnl += (cur_val - pos["cost_basis"])

        net_pnl = realized_pnl + unrealized_pnl - s["fees_paid"]
        strategy_report.append(
            {
                "strategy": strat_name,
                "orders": s["orders"],
                "fills": s["fills"],
                "fill_rate": s["fill_rate"],
                "buy_fills": s["buy_fills"],
                "sell_fills": s["sell_fills"],
                "buy_vol": s["buy_vol"],
                "sell_vol": s["sell_vol"],
                "fees": s["fees_paid"],
                "active_markets": active_positions_cnt,
                "cost_basis": total_cost_basis,
                "mtm_val": mtm_position_val,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "net_pnl": net_pnl,
                "equity": 100.0 + net_pnl,
                "return_pct": net_pnl / 100.0 * 100.0,
                "reject_reasons": dict(s["reject_reasons"]),
            }
        )

    strategy_report.sort(key=lambda x: x["net_pnl"], reverse=True)

    # 5. Open positions list
    pos_rows = conn.execute(
        """
        SELECT venue, market_id, quantity, average_cost, realized_pnl, settled
        FROM paper_positions
        WHERE quantity > 0
        """
    ).fetchall()

    positions = []
    for r in pos_rows:
        positions.append(
            {
                "market_id": r["market_id"],
                "quantity": float(r["quantity"]),
                "average_cost": float(r["average_cost"]),
                "realized_pnl": float(r["realized_pnl"]),
                "settled": bool(r["settled"]),
            }
        )

    conn.close()

    total_return_pct = ((latest_equity - starting_equity) / starting_equity) * 100.0 if starting_equity > 0 else 0.0

    return {
        "database": str(db_path),
        "snapshots": {
            "total": snapshot_count,
            "quoted": quoted_count,
            "markets": markets_count,
            "resolved": resolved_count,
            "quote_coverage_pct": (quoted_count / snapshot_count * 100.0) if snapshot_count > 0 else 0.0,
        },
        "portfolio": {
            "starting_equity": starting_equity,
            "latest_equity": latest_equity,
            "cash": latest_cash,
            "positions_value": latest_positions_val,
            "total_fees": total_fees,
            "total_return_pct": total_return_pct,
            "max_drawdown_dollars": max_drawdown_dollars,
            "max_drawdown_pct": max_drawdown_pct,
            "equity_marks_count": len(equity_rows),
        },
        "strategies": strategy_report,
        "open_positions": positions,
    }


def print_report(data: dict) -> None:
    p = data["portfolio"]
    s = data["snapshots"]

    print("=" * 86)
    print("           PREDICTION MARKET PAPER TRADING ALPHA ATTRIBUTION REPORT           ")
    print("=" * 86)
    print(f"Database: {data['database']}")
    print(
        f"Snapshots: {s['total']:,} total | {s['quoted']:,} quoted ({s['quote_coverage_pct']:.2f}% coverage) across {s['markets']} markets"
    )
    print("-" * 86)
    print("STRATEGY LEADERBOARD & PERFORMANCE ATTRIBUTION:")
    print(
        f"  {'Rank':<5} {'Strategy':<26} {'Net Alpha':<11} {'Return %':<10} {'Fills':<8} {'Buys/Sells':<12} {'Fees':<9}"
    )
    print("  " + "-" * 82)
    for idx, st in enumerate(data["strategies"], start=1):
        buys_sells = f"{st['buy_fills']}/{st['sell_fills']}"
        ret_sign = "+" if st["return_pct"] >= 0 else ""
        print(
            f"  #{idx:<4} {st['strategy']:<26} ${st['net_pnl']:<10.2f} {ret_sign}{st['return_pct']:<9.2f}% {st['fills']:<8} {buys_sells:<12} ${st['fees']:<8.4f}"
        )
        if st["reject_reasons"]:
            reasons_str = ", ".join(f"{k}: {v:,}" for k, v in sorted(st["reject_reasons"].items(), key=lambda x: x[1], reverse=True)[:3])
            print(f"       └─ Top Rejections: {reasons_str}")

    print("-" * 86)
    print(f"PORTFOLIO OVERALL OPEN POSITIONS ({len(data['open_positions'])}):")
    if not data["open_positions"]:
        print("  None")
    else:
        print(f"  {'Market Ticker':<45} {'Qty':<6} {'Avg Cost':<10} {'Realized P&L':<12}")
        print("  " + "-" * 75)
        for pos in data["open_positions"][:20]:
            print(
                f"  {pos['market_id']:<45} {pos['quantity']:<6.1f} ${pos['average_cost']:<9.3f} ${pos['realized_pnl']:<11.3f}"
            )
        if len(data["open_positions"]) > 20:
            print(f"  ... and {len(data['open_positions']) - 20} more open positions")
    print("=" * 86)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze prediction market paper trading performance.")
    parser.add_argument("database", nargs="?", default=None, help="SQLite database path")
    parser.add_argument("--db", dest="db_flag", default=None, help="SQLite database path (alias for positional database)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    target_db = args.db_flag or args.database or "data/oracle_latest.sqlite"
    data = analyze_database(target_db)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_report(data)


if __name__ == "__main__":
    main()
