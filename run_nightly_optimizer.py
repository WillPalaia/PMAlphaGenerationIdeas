"""Nightly autonomous quant optimizer and reporting runner.

Executes mathematical parameter auto-tuning, generates daily markdown reports,
verifies unit tests, and reloads the live paper trading service.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from pm_alpha.optimizer import run_optimization_cycle


def main() -> None:
    parser = argparse.ArgumentParser(description="Run autonomous nightly quant optimizer.")
    parser.add_argument(
        "--db",
        default="/var/lib/pm-alpha/market_data.sqlite",
        help="Path to SQLite market database",
    )
    parser.add_argument(
        "--config",
        default="/opt/pm-alpha/strategy_config.json",
        help="Path to strategy config JSON",
    )
    parser.add_argument(
        "--reports-dir",
        default="/var/lib/pm-alpha/reports",
        help="Directory to save daily markdown reports",
    )
    parser.add_argument(
        "--restart-service",
        action="store_true",
        help="Verify pytest and restart pm-alpha-paper service if tests pass",
    )
    args = parser.parse_args()

    print("============================================================")
    print("      AUTONOMOUS NIGHTLY PREDICTION MARKET OPTIMIZER        ")
    print("============================================================")

    # 1. Run optimization cycle
    result = run_optimization_cycle(
        db_path=args.db,
        config_path=args.config,
        reports_dir=args.reports_dir,
    )

    print(f"\n[1/3] Performance audit complete for {result['date']}.")
    print(f"      Saved report to: {result['report_saved']}")
    print(f"      Saved config to: {result['config_saved']}")

    # 2. Show adjustments
    print("\n[2/3] Parameter Adjustments:")
    if result["adjustments"]:
        for adj in result["adjustments"]:
            print(f"  * {adj}")
    else:
        print("  * No parameter changes required. Parameters operating optimally.")

    # 3. Test & Reload Service if requested
    if args.restart_service:
        print("\n[3/3] Running pre-flight unit tests (pytest)...")
        test_res = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-o", "cache_dir=/tmp/.pytest_cache"],
            cwd=str(Path(__file__).parent),
            capture_output=True,
            text=True,
        )
        if test_res.returncode != 0:
            print("ERROR: Unit tests failed! Service reload aborted.", file=sys.stderr)
            print(test_res.stdout, file=sys.stderr)
            sys.exit(1)
        print("      All unit tests passed! Reloading pm-alpha-paper service...")
        subprocess.run(["sudo", "systemctl", "restart", "pm-alpha-paper"], check=True)
        print("      Service pm-alpha-paper restarted cleanly with updated parameters.")
    else:
        print("\n[3/3] Service restart not requested. Run complete.")

    print("\n============================================================")
    print("Report Summary:")
    print("============================================================")
    print(result["report_md"])


if __name__ == "__main__":
    main()
