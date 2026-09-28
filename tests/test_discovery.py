from unittest.mock import patch

from pm_alpha.discovery import KalshiMarketDiscovery


def test_discovery_excludes_multivariate_markets():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    payload = {
        "markets": [
            {"ticker": "KXMV-1", "market_type": "binary"},
            {
                "ticker": "KXGAME-1",
                "title": "Team wins",
                "market_type": "binary",
                "liquidity_dollars": "2.5",
                "volume": "3",
                "open_interest": "4",
            },
        ]
    }
    with patch("pm_alpha.discovery.urlopen") as urlopen, patch(
        "pm_alpha.discovery.json.load", return_value=payload
    ):
        urlopen.return_value = Response()
        markets = KalshiMarketDiscovery()._discover(10)
    assert [market.ticker for market in markets] == ["KXGAME-1"]


def test_check_settlements():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    payload = {
        "markets": [
            {"ticker": "KX-ACTIVE", "status": "active", "result": ""},
            {"ticker": "KX-WON", "status": "finalized", "result": "yes"},
            {"ticker": "KX-LOST", "status": "determined", "result": "no"},
        ]
    }
    with patch("pm_alpha.discovery.urlopen") as urlopen, patch(
        "pm_alpha.discovery.json.load", return_value=payload
    ):
        urlopen.return_value = Response()
        settled = KalshiMarketDiscovery()._check_settlements(["KX-ACTIVE", "KX-WON", "KX-LOST"])
    assert settled == [("KX-WON", True), ("KX-LOST", False)]


def test_classify_market():
    from pm_alpha.discovery import classify_market

    assert classify_market("KXGOLDH-26SEP2801-T4159.99") == "macro_finance"
    assert classify_market("KXINFLATION-26SEP") == "macro_finance"
    assert classify_market("KXTEMPCHIHS-26SEP28-T78") == "weather"
    assert classify_market("KXRAIN-NY-26OCT") == "weather"
    assert classify_market("KXNFLGAME-26OCT01-NYGDAL") == "sports"
    assert classify_market("KXATPCHALLENGERMATCH-26SEP28") == "sports"
    assert classify_market("KXCS2MAP-26SEP28-HERFAL") == "esports"
    assert classify_market("KXPRES-2028-DEM") == "politics"
    assert classify_market("KXUNKNOWN-12345") == "other"


def test_discovery_balanced_quotas_and_close_time():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    # Create dummy markets across categories
    markets_payload = []
    # 5 macro
    for i in range(5):
        markets_payload.append({
            "ticker": f"KXGOLDH-{i}",
            "market_type": "binary",
            "volume_24h_fp": "100",
            "close_time": "2026-09-28T05:00:00Z" if i == 0 else "2026-10-28T05:00:00Z",
        })
    # 5 sports
    for i in range(5):
        markets_payload.append({
            "ticker": f"KXNFL-{i}",
            "market_type": "binary",
            "volume_24h_fp": "100",
            "close_time": "2026-09-28T06:00:00Z" if i == 0 else "2026-10-28T05:00:00Z",
        })
    # 5 weather
    for i in range(5):
        markets_payload.append({
            "ticker": f"KXTEMP-{i}",
            "market_type": "binary",
            "volume_24h_fp": "100",
            "close_time": "2026-09-28T07:00:00Z" if i == 0 else "2026-10-28T05:00:00Z",
        })

    with patch("pm_alpha.discovery.urlopen") as urlopen, patch(
        "pm_alpha.discovery.json.load", return_value={"markets": markets_payload}
    ):
        urlopen.return_value = Response()
        disc = KalshiMarketDiscovery()._discover(limit=6)

    assert len(disc) == 6
    categories = {m.category for m in disc}
    assert "macro_finance" in categories
    assert "sports" in categories
    assert "weather" in categories
    # Near-term closing contracts should be included
    tickers = {m.ticker for m in disc}
    assert "KXGOLDH-0" in tickers
    assert "KXNFL-0" in tickers
    assert "KXTEMP-0" in tickers

