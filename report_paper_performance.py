"""Comprehensive strategy attribution and paper performance report.

Inspects SQLite capture databases and outputs strategy-level P&L, fill rates,
equity curves, drawdowns, and market exposure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
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

    # 3. Strategy performance breakdown
    # Check if strategy column exists in paper_orders
    order_cols = [c["name"] for c in conn.execute("PRAGMA table_info(paper_orders)").fetchall()]
    has_strat_col = "strategy" in order_cols

    strat_field = "COALESCE(NULLIF(strategy, ''), signal)" if has_strat_col else "signal"
    order_rows = conn.execute(
        f"""
        SELECT {strat_field} as strategy_name,
               status,
               reject_reason,
               COUNT(1) as cnt,
               SUM(quantity) as requested_qty,
               SUM(filled_quantity) as filled_qty
        FROM paper_orders
        GROUP BY {strat_field}, status, reject_reason
        """
    ).fetchall()

    strategies: dict[str, dict] = {}
    for r in order_rows:
        name = r["strategy_name"] or "unknown"
        s = strategies.setdefault(
            name,
            {
                "orders": 0,
                "fills": 0,
                "rejections": 0,
                "reject_reasons": {},
                "filled_quantity": 0.0,
                "fees_paid": 0.0,
                "fill_rate": 0.0,
            },
        )
        s["orders"] += r["cnt"]
        if r["status"] == "filled":
            s["fills"] += r["cnt"]
            s["filled_quantity"] += float(r["filled_qty"] or 0)
        else:
            s["rejections"] += r["cnt"]
            reason = r["reject_reason"] or "unknown"
            s["reject_reasons"][reason] = s["reject_reasons"].get(reason, 0) + r["cnt"]

    # Fills and fees per strategy
    if has_strat_col:
        fills_query = f"""
            SELECT COALESCE(NULLIF(o.strategy, ''), o.signal) as strategy_name,
                   COUNT(f.id) as fill_count,
                   SUM(f.fee) as total_fee,
                   SUM(f.quantity * f.price) as gross_notional
            FROM paper_fills f
            JOIN paper_orders o ON f.order_id = o.client_order_id
            GROUP BY COALESCE(NULLIF(o.strategy, ''), o.signal)
        """
    else:
        fills_query = """
            SELECT o.signal as strategy_name,
                   COUNT(f.id) as fill_count,
                   SUM(f.fee) as total_fee,
                   SUM(f.quantity * f.price) as gross_notional
            FROM paper_fills f
            JOIN paper_orders o ON f.order_id = o.client_order_id
            GROUP BY o.signal
        """

    for r in conn.execute(fills_query).fetchall():
        name = r["strategy_name"] or "unknown"
        if name in strategies:
            strategies[name]["fees_paid"] = float(r["total_fee"] or 0.0)
            strategies[name]["gross_notional"] = float(r["gross_notional"] or 0.0)

    for s in strategies.values():
        s["fill_rate"] = (s["fills"] / s["orders"] * 100.0) if s["orders"] > 0 else 0.0

    # 4. Open positions
    pos_rows = conn.execute(
        """
        SELECT venue, market_id, quantity, average_cost, realized_pnl, settled
        FROM paper_positions
        WHERE quantity > 0 OR settled = 1
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

    # 5. Check if paper_strategy_equity exists
    has_strat_equity = conn.execute(
        "SELECT COUNT(1) FROM sqlite_master WHERE type='table' AND name='paper_strategy_equity'"
    ).fetchone()[0] > 0

    strategy_equity_summary = {}
    if has_strat_equity:
        for r in conn.execute(
            """
            SELECT strategy, cash, positions_value, equity, fees
            FROM paper_strategy_equity
            WHERE id IN (SELECT MAX(id) FROM paper_strategy_equity GROUP BY strategy)
            """
        ).fetchall():
            strategy_equity_summary[r["strategy"]] = {
                "cash": float(r["cash"]),
                "positions_value": float(r["positions_value"]),
                "equity": float(r["equity"]),
                "fees": float(r["fees"]),
            }

    conn.close()

    total_return_pct = ((latest_equity - starting_equity) / starting_equity) * 100.0 if starting_equity > 0 else 0.0

    return {
        "database": str(db_path),
        "snapshots": {
            "total": snapshot_count,
            "quoted": quoted_count,
            "markets": markets_count,
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
        "strategies": strategies,
        "strategy_equity": strategy_equity_summary,
        "open_positions": positions,
    }


def print_report(data: dict) -> None:
    p = data["portfolio"]
    s = data["snapshots"]

    print("=" * 80)
    print("           PREDICTION MARKET PAPER TRADING ALPHA REPORT           ")
    print("=" * 80)
    print(f"Database: {data['database']}")
    print(f"Snapshots: {s['total']:,} total | {s['quoted']:,} quoted ({s['quote_coverage_pct']:.2f}% coverage) across {s['markets']} markets")
    print("-" * 80)
    print("PORTFOLIO SUMMARY:")
    print(f"  Starting Equity:     ${p['starting_equity']:,.2f}")
    print(f"  Current Equity:      ${p['latest_equity']:,.2f}  ({p['total_return_pct']:+.2f}%)")
    print(f"  Available Cash:      ${p['cash']:,.2f}")
    print(f"  Open Positions MTM:  ${p['positions_value']:,.2f}")
    print(f"  Cumulative Fees:     ${p['total_fees']:,.4f}")
    print(f"  Max Drawdown:        ${p['max_drawdown_dollars']:,.2f}  ({p['max_drawdown_pct']:.2f}%)")
    print(f"  Total Portfolio Marks: {p['equity_marks_count']:,}")
    print("-" * 80)
    print("STRATEGY LEADERBOARD & ATTRIBUTION:")
    print(f"  {'Strategy / Signal':<26} {'Orders':<8} {'Fills':<8} {'Fill %':<8} {'Qty':<8} {'Fees Paid':<12}")
    print("  " + "-" * 72)
    for name, st in sorted(data["strategies"].items(), key=lambda x: x[1]["fills"], reverse=True):
        print(
            f"  {name:<26} {st['orders']:<8} {st['fills']:<8} {st['fill_rate']:<7.1f}% {st['filled_quantity']:<8.1f} ${st['fees_paid']:<10.4f}"
        )
        if st["reject_reasons"]:
            reasons_str = ", ".join(f"{k}: {v}" for k, v in st["reject_reasons"].items())
            print(f"    └─ Rejections: {reasons_str}")

    if data["strategy_equity"]:
        print("-" * 80)
        print("PER-STRATEGY EQUITY SUB-PORTFOLIOS:")
        print(f"  {'Strategy':<26} {'Cash':<12} {'Positions':<12} {'Equity':<12} {'Fees':<10}")
        print("  " + "-" * 72)
        for name, seq in sorted(data["strategy_equity"].items()):
            print(
                f"  {name:<26} ${seq['cash']:<11.2f} ${seq['positions_value']:<11.2f} ${seq['equity']:<11.2f} ${seq['fees']:<9.4f}"
            )

    print("-" * 80)
    print(f"OPEN POSITIONS ({len(data['open_positions'])}):")
    if not data["open_positions"]:
        print("  None")
    else:
        print(f"  {'Market Ticker':<45} {'Qty':<6} {'Avg Cost':<10} {'Realized P&L':<12}")
        print("  " + "-" * 75)
        for pos in data["open_positions"]:
            print(
                f"  {pos['market_id']:<45} {pos['quantity']:<6.1f} ${pos['average_cost']:<9.3f} ${pos['realized_pnl']:<11.3f}"
            )
    print("=" * 80)


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
