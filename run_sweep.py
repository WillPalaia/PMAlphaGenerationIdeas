"""Run a broad offline strategy sweep over one or more normalized CSV files."""

import argparse
import json
from pathlib import Path

from pm_alpha.historical import load_snapshots_csv
from pm_alpha.storage import SnapshotStore
from pm_alpha.sweep import run_sweep, write_csv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_files", nargs="*")
    parser.add_argument("--sqlite")
    parser.add_argument("--output", default="data/sweep.csv")
    args = parser.parse_args()

    markets = {}
    if bool(args.sqlite) == bool(args.csv_files):
        parser.error("provide either CSV files or --sqlite, but not both")
    if args.sqlite:
        store = SnapshotStore(args.sqlite)
        for venue, market_id in store.markets():
            snapshots = store.load(venue, market_id)
            if snapshots:
                markets[f"{venue}:{market_id}"] = snapshots
    else:
        for file_name in args.csv_files:
            snapshots = load_snapshots_csv(file_name)
            if not snapshots:
                raise ValueError(f"no snapshots found in {file_name}")
            markets[Path(file_name).stem] = snapshots
    if not markets:
        raise ValueError("no markets found in input")
    rows = run_sweep(markets)
    write_csv(rows, args.output)
    print(json.dumps({
        "markets": len(markets),
        "experiments": len(rows),
        "output": args.output,
    }, indent=2))


if __name__ == "__main__":
    main()
