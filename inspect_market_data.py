"""Print coverage and liquidity diagnostics for an Oracle SQLite capture."""

import argparse
import json
from dataclasses import asdict

from pm_alpha.report import inspect_database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    args = parser.parse_args()
    reports = inspect_database(args.database)
    print(json.dumps([asdict(report) for report in reports], indent=2))


if __name__ == "__main__":
    main()
