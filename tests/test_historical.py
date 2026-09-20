import json
from unittest.mock import patch

import pytest

from pm_alpha.historical import fetch_kalshi_candlesticks, load_snapshots_csv


def test_load_snapshots_csv_rejects_missing_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("timestamp_ms,venue\n1,kalshi\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required"):
        load_snapshots_csv(path)


def test_fetch_kalshi_candles_normalizes_percent_prices():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    with patch(
        "pm_alpha.historical.urlopen"
    ) as urlopen, patch(
        "pm_alpha.historical.json.load",
        return_value={"candlesticks": [{"end_ts": 100, "yes": 35}]},
    ):
        urlopen.return_value = Response()
        snapshots = fetch_kalshi_candlesticks("T", start_ts=1, end_ts=200)

    assert snapshots[0].timestamp_ms == 100_000
    assert snapshots[0].yes_bid == pytest.approx(0.345)
    assert snapshots[0].yes_ask == pytest.approx(0.355)
