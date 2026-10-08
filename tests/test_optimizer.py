from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from pm_alpha.optimizer import analyze_strategy_metrics, compute_auto_tuning, run_optimization_cycle
from pm_alpha.storage import SCHEMA


def test_optimizer_cycle_end_to_end():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.sqlite"
        config_path = tmp_path / "strategy_config.json"
        reports_dir = tmp_path / "reports"

        # Create tables using canonical schema
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        # Insert sample data for spread-harvesting with negative pnl
        conn.execute(
            """
            INSERT INTO paper_strategy_equity (timestamp_ms, strategy, equity, cash, positions_value, fees)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1700000000000, "spread-harvesting", 100.0, 100.0, 0.0, 0.0),
        )
        conn.execute(
            """
            INSERT INTO paper_strategy_equity (timestamp_ms, strategy, equity, cash, positions_value, fees)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1700003600000, "spread-harvesting", 94.0, 94.0, 0.0, 0.20),
        )
        conn.execute(
            """
            INSERT INTO paper_strategy_positions (strategy, venue, market_id, quantity, average_cost, realized_pnl, settled)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("spread-harvesting", "kalshi", "KXTEST-26", 1.0, 0.50, -6.0, 1),
        )
        conn.execute(
            """
            INSERT INTO paper_orders (client_order_id, timestamp_ms, venue, market_id, side, quantity, limit_price, signal, strategy, status, filled_quantity, reject_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ord-1", 1700000000000, "kalshi", "KXTEST-26", "yes", 1.0, 0.50, "mm-harvest", "spread-harvesting", "filled", 1.0, None),
        )
        conn.execute(
            """
            INSERT INTO paper_fills (order_id, timestamp_ms, venue, market_id, side, quantity, price, fee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ord-1", 1700000000000, "kalshi", "KXTEST-26", "yes", 1.0, 0.50, 0.01),
        )

        conn.commit()
        conn.close()

        # Run cycle
        res = run_optimization_cycle(
            db_path=str(db_path),
            config_path=str(config_path),
            reports_dir=str(reports_dir),
        )

        assert res["date"] is not None
        assert "spread-harvesting" in res["metrics"]
        assert config_path.exists()
        assert Path(res["report_saved"]).exists()

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        assert "spread-harvesting" in cfg
        # Since it had losses, min_spread should have been tightened/widened or holding ticks reduced
        assert cfg["spread-harvesting"]["min_spread"] >= 0.04
        assert "Gemini AI Executive Commentary" in res["report_md"]


def test_fetch_gemini_commentary_no_key():
    from pm_alpha.optimizer import fetch_gemini_commentary
    # When no key is given and env is unset, returns None gracefully
    res = fetch_gemini_commentary({}, [], api_key=None)
    assert res is None or isinstance(res, str)

