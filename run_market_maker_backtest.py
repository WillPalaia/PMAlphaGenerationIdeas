"""Backtest conservative external-reference quotes from two normalized CSV files."""

import argparse
import csv

from pm_alpha.historical import load_snapshots_csv
from pm_alpha.market_making import (
    MarketMakerConfig,
    ReferencePrice,
    simulate_reference_market_maker,
)


def load_references(path: str) -> list[ReferencePrice]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp_ms", "market_id", "fair_probability", "source"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"reference CSV is missing: {', '.join(sorted(missing))}")
        return [
            ReferencePrice(
                timestamp_ms=int(row["timestamp_ms"]),
                market_id=row["market_id"],
                fair_probability=float(row["fair_probability"]),
                source=row["source"],
            )
            for row in reader
        ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--references", required=True)
    parser.add_argument("--half-spread", type=float, default=0.03)
    parser.add_argument("--max-inventory", type=float, default=5.0)
    parser.add_argument("--quote-size", type=float, default=1.0)
    parser.add_argument("--fee-rate", type=float, default=0.0)
    parser.add_argument("--adverse-selection-bps", type=float, default=0.0)
    args = parser.parse_args()
    result = simulate_reference_market_maker(
        load_snapshots_csv(args.snapshots),
        load_references(args.references),
        MarketMakerConfig(
            half_spread=args.half_spread,
            max_inventory=args.max_inventory,
            quote_size=args.quote_size,
            fee_rate=args.fee_rate,
            adverse_selection_bps=args.adverse_selection_bps,
        ),
    )
    print(result)


if __name__ == "__main__":
    main()
