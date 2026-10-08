"""Autonomous mathematical strategy optimizer and performance reporting engine.

Analyzes paper trading execution metrics from SQLite, computes statistical parameter
tuning adjustments, and outputs structured daily reports without requiring LLM API calls.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional


def analyze_strategy_metrics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Extract performance and execution metrics per strategy from SQLite."""
    conn.row_factory = sqlite3.Row
    metrics: Dict[str, Dict[str, Any]] = {}

    strategies = [r[0] for r in conn.execute("SELECT DISTINCT strategy FROM paper_strategy_equity").fetchall()]
    if not strategies:
        # Fallback to order signals if no strategy equity records yet
        strategies = [r[0] for r in conn.execute("SELECT DISTINCT strategy FROM paper_orders WHERE strategy IS NOT NULL").fetchall()]

    for strat in strategies:
        # Equity metrics
        first_eq = conn.execute(
            "SELECT equity, cash, positions_value, fees FROM paper_strategy_equity WHERE strategy = ? ORDER BY id ASC LIMIT 1",
            (strat,),
        ).fetchone()
        last_eq = conn.execute(
            "SELECT equity, cash, positions_value, fees FROM paper_strategy_equity WHERE strategy = ? ORDER BY id DESC LIMIT 1",
            (strat,),
        ).fetchone()

        start_equity = float(first_eq["equity"]) if first_eq else 100.0
        latest_equity = float(last_eq["equity"]) if last_eq else start_equity
        latest_cash = float(last_eq["cash"]) if last_eq else start_equity
        latest_pos_val = float(last_eq["positions_value"]) if last_eq else 0.0
        fees = float(last_eq["fees"]) if last_eq else 0.0
        net_pnl = latest_equity - start_equity
        ret_pct = (net_pnl / start_equity) * 100.0 if start_equity > 0 else 0.0

        # Settlement metrics
        settled_rows = conn.execute(
            "SELECT realized_pnl FROM paper_strategy_positions WHERE strategy = ? AND settled = 1",
            (strat,),
        ).fetchall()
        settled_cnt = len(settled_rows)
        settled_wins = sum(1 for r in settled_rows if r["realized_pnl"] > 0)
        settled_losses = sum(1 for r in settled_rows if r["realized_pnl"] < 0)
        settled_win_rate = (settled_wins / settled_cnt * 100.0) if settled_cnt > 0 else 0.0
        settled_pnl = sum(float(r["realized_pnl"]) for r in settled_rows)

        # Active trading metrics
        active_pnl_row = conn.execute(
            "SELECT SUM(realized_pnl) FROM paper_strategy_positions WHERE strategy = ? AND settled = 0",
            (strat,),
        ).fetchone()
        active_pnl = float(active_pnl_row[0] or 0.0) if active_pnl_row else 0.0

        # Orders & Fills
        orders_cnt = conn.execute(
            "SELECT COUNT(1) FROM paper_orders WHERE strategy = ?", (strat,)
        ).fetchone()[0]
        fills = conn.execute(
            """
            SELECT f.side, f.quantity, f.price, f.fee
            FROM paper_fills f
            JOIN paper_orders o ON f.order_id = o.client_order_id
            WHERE o.strategy = ?
            """,
            (strat,),
        ).fetchall()

        buys = sum(1 for f in fills if f["side"] == "buy")
        sells = sum(1 for f in fills if f["side"] == "sell")
        buy_vol = sum(float(f["quantity"]) * float(f["price"]) for f in fills if f["side"] == "buy")
        sell_vol = sum(float(f["quantity"]) * float(f["price"]) for f in fills if f["side"] == "sell")

        # Open positions count
        open_pos_cnt = conn.execute(
            "SELECT COUNT(1) FROM paper_strategy_positions WHERE strategy = ? AND quantity > 0 AND settled = 0",
            (strat,),
        ).fetchone()[0]

        metrics[strat] = {
            "start_equity": start_equity,
            "latest_equity": latest_equity,
            "latest_cash": latest_cash,
            "positions_val": latest_pos_val,
            "fees": fees,
            "net_pnl": net_pnl,
            "return_pct": ret_pct,
            "orders": orders_cnt,
            "fills": len(fills),
            "buys": buys,
            "sells": sells,
            "buy_vol": buy_vol,
            "sell_vol": sell_vol,
            "settled_cnt": settled_cnt,
            "settled_wins": settled_wins,
            "settled_losses": settled_losses,
            "settled_win_rate": settled_win_rate,
            "settled_pnl": settled_pnl,
            "active_pnl": active_pnl,
            "open_positions": open_pos_cnt,
        }

    return metrics


def compute_auto_tuning(
    metrics: Dict[str, Any], current_config: Dict[str, Any]
) -> tuple[Dict[str, Any], list[str]]:
    """Compute mathematical parameter adjustments based on empirical metrics."""
    new_config = json.loads(json.dumps(current_config))  # Deep copy
    adjustments: list[str] = []

    # 1. Spread-Harvesting Auto-Tuning
    sh_cfg = new_config.setdefault("spread-harvesting", {})
    sh_cfg.setdefault("min_spread", 0.04)
    sh_cfg.setdefault("max_spread", 0.10)
    sh_cfg.setdefault("inventory_skew", 0.02)
    sh_cfg.setdefault("aggressiveness", 0.40)
    sh_cfg.setdefault("min_profit", 0.02)
    sh_cfg.setdefault("min_price", 0.20)
    sh_cfg.setdefault("max_price", 0.80)
    sh_cfg.setdefault("max_holding_ticks", 60)
    sh_cfg.setdefault("stop_loss_margin", 0.06)

    sh_m = metrics.get("spread-harvesting", {})
    if sh_m:
        # Check if settlement losses occurred; if so, enforce strict square-off
        if sh_m.get("settled_losses", 0) > 0:
            current_stop = sh_cfg.get("stop_loss_margin", 0.06)
            sh_cfg["stop_loss_margin"] = min(current_stop, 0.05)
            sh_cfg["max_holding_ticks"] = min(sh_cfg.get("max_holding_ticks", 60), 50)
            adjustments.append(
                f"[spread-harvesting] Enforced tight square-off: stop_loss_margin={sh_cfg['stop_loss_margin']}, max_holding_ticks={sh_cfg['max_holding_ticks']} (prevented binary settlement leaks)"
            )
        # If active trading is solidly profitable and win rate high, ensure min_profit captures spread
        if sh_m.get("active_pnl", 0.0) > 1.0:
            sh_cfg["min_profit"] = max(0.02, sh_cfg.get("min_profit", 0.02))

    # 2. Conservative-Yield Auto-Tuning
    cy_cfg = new_config.setdefault("conservative-yield", {})
    cy_cfg.setdefault("min_price", 0.88)
    cy_cfg.setdefault("max_price", 0.95)
    cy_cfg.setdefault("max_spread", 0.025)
    cy_cfg.setdefault("lookback", 6)
    cy_cfg.setdefault("max_range", 0.03)

    cy_m = metrics.get("conservative-yield", {})
    if cy_m:
        win_rate = cy_m.get("settled_win_rate", 100.0)
        settled_cnt = cy_m.get("settled_cnt", 0)
        if settled_cnt >= 5 and win_rate == 100.0:
            # High certainty validated: keep bounded favorite range
            cy_cfg["min_price"] = 0.88
            cy_cfg["max_price"] = 0.95
            cy_cfg["max_spread"] = 0.025
        elif win_rate < 90.0 and settled_cnt > 0:
            # Loss occurred: raise minimum confidence threshold
            cy_cfg["min_price"] = max(0.90, cy_cfg.get("min_price", 0.88) + 0.01)
            adjustments.append(
                f"[conservative-yield] Raised min_price to {cy_cfg['min_price']} following non-100% settlement"
            )

    # 3. Microstructure-Herd-Fader Auto-Tuning
    mh_cfg = new_config.setdefault("microstructure-herd-fader", {})
    mh_m = metrics.get("microstructure-herd-fader", {})
    if mh_m:
        ret_pct = mh_m.get("return_pct", 0.0)
        if ret_pct < -2.0:
            # Tighten target price to only fade extreme lottery longshots
            mh_cfg["max_target_price"] = min(0.30, mh_cfg.get("max_target_price", 0.35) - 0.02)
            mh_cfg["min_depth_absorption"] = max(2.5, mh_cfg.get("min_depth_absorption", 2.0) + 0.5)
            adjustments.append(
                f"[microstructure-herd-fader] Tightened fade filters: max_target_price={mh_cfg['max_target_price']}, min_depth_absorption={mh_cfg['min_depth_absorption']}"
            )
        else:
            mh_cfg.setdefault("max_target_price", 0.35)
            mh_cfg.setdefault("take_profit", 0.04)
            mh_cfg.setdefault("stop_loss", 0.05)
            mh_cfg.setdefault("max_holding_ticks", 40)

    # 4. Complement-Arbitrage (Zero risk, defaults)
    ca_cfg = new_config.setdefault("complement-arbitrage", {})
    ca_cfg.setdefault("min_edge", 0.0)
    ca_cfg.setdefault("quantity", 1.0)

    return new_config, adjustments


def generate_markdown_report(
    date_str: str,
    metrics: Dict[str, Any],
    adjustments: list[str],
    config: Dict[str, Any],
) -> str:
    """Generate a clean daily performance audit and auto-tuning report."""
    lines = [
        f"# Daily Prediction Market Quant Report: {date_str}",
        f"**Generated:** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        "**Host:** Oracle Cloud VM (`157.151.132.129`, Service: `pm-alpha-paper`)  ",
        "",
        "## 1. Strategy Leaderboard & Attribution",
        "",
        "| Strategy | Equity | Net P&L | Return % | Fills (B/S) | Settled (W/L) | Win Rate | Fees Paid | Open Pos |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for strat, m in sorted(metrics.items(), key=lambda x: x[1].get("net_pnl", 0.0), reverse=True):
        buys_sells = f"{m['buys']}/{m['sells']}"
        settled_str = f"{m['settled_cnt']} ({m['settled_wins']}/{m['settled_losses']})"
        wr_str = f"{m['settled_win_rate']:.1f}%" if m['settled_cnt'] > 0 else "N/A"
        sign = "+" if m['net_pnl'] >= 0 else ""
        lines.append(
            f"| **`{strat}`** | **${m['latest_equity']:.2f}** | {sign}${m['net_pnl']:.2f} | {sign}{m['return_pct']:.2f}% | {m['fills']} ({buys_sells}) | {settled_str} | {wr_str} | ${m['fees']:.4f} | {m['open_positions']} |"
        )

    lines.extend([
        "",
        "## 2. Statistical Auto-Tuning Decisions",
        "",
    ])

    if adjustments:
        for adj in adjustments:
            lines.append(f"- {adj}")
    else:
        lines.append("- All strategies operated within optimal statistical envelopes. Current parameters confirmed.")

    lines.extend([
        "",
        "## 3. Active Parameter Configuration",
        "```json",
        json.dumps(config, indent=2),
        "```",
        "",
    ])

    return "\n".join(lines)


def run_optimization_cycle(
    db_path: str = "/var/lib/pm-alpha/market_data.sqlite",
    config_path: str = "/opt/pm-alpha/strategy_config.json",
    reports_dir: str = "/var/lib/pm-alpha/reports",
) -> Dict[str, Any]:
    """Execute complete end-to-end nightly optimization and reporting cycle."""
    conn = sqlite3.connect(db_path)
    metrics = analyze_strategy_metrics(conn)
    conn.close()

    # Load existing config if available
    cfg_file = Path(config_path)
    current_cfg: Dict[str, Any] = {}
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                current_cfg = json.load(f)
        except Exception:
            current_cfg = {}

    # Compute mathematical auto-tuning
    new_cfg, adjustments = compute_auto_tuning(metrics, current_cfg)

    # Save updated config
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(new_cfg, f, indent=2)

    # Generate and save daily report
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    rep_dir = Path(reports_dir)
    rep_dir.mkdir(parents=True, exist_ok=True)
    report_file = rep_dir / f"daily_report_{date_str}.md"

    report_md = generate_markdown_report(date_str, metrics, adjustments, new_cfg)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_md)

    return {
        "date": date_str,
        "metrics": metrics,
        "adjustments": adjustments,
        "config_saved": str(cfg_file),
        "report_saved": str(report_file),
        "report_md": report_md,
    }
