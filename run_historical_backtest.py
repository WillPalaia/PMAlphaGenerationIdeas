"""Run a deliberately simple historical replay against a CSV or Kalshi candles."""

import argparse
from pm_alpha.backtest import BacktestConfig, EventDrivenBacktester
from pm_alpha.historical import fetch_kalshi_candlesticks, load_snapshots_csv
from pm_alpha.metrics import summarize
from pm_alpha.strategies import BuyBelowThreshold


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv")
    source.add_argument("--ticker")
    parser.add_argument("--start-ts", type=int)
    parser.add_argument("--end-ts", type=int)
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--quantity", type=float, default=1.0)
    parser.add_argument("--fee-rate", type=float, default=0.0)
    args = parser.parse_args()

    if args.csv:
        snapshots = load_snapshots_csv(args.csv)
    else:
        if args.start_ts is None or args.end_ts is None:
            parser.error("--ticker requires --start-ts and --end-ts")
        snapshots = fetch_kalshi_candlesticks(
            args.ticker, start_ts=args.start_ts, end_ts=args.end_ts
        )
    result = EventDrivenBacktester(
        BacktestConfig(taker_fee_rate=args.fee_rate)
    ).run(snapshots, BuyBelowThreshold(args.threshold, args.quantity))
    print(summarize(result))
    print(result)


if __name__ == "__main__":
    main()
