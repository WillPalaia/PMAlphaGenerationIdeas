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


def test_discovery_filters_virtual_esports_and_prioritizes_human_esports():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    # Markets payload with virtual esports and real human esports
    payload = {
        "markets": [
            # Virtual esports with zero volume
            {
                "ticker": "KXESOCCERGAME-26OCT15-MANCHEL",
                "title": "Virtual eSoccer",
                "market_type": "binary",
                "volume_24h_fp": "0",
                "liquidity_dollars": "0",
                "open_interest_fp": "0",
            },
            {
                "ticker": "KXEBASKETBALLGAME-26OCT15-LALBOS",
                "title": "Virtual eBasketball",
                "market_type": "binary",
                "volume_24h_fp": "0",
                "liquidity_dollars": "0",
                "open_interest_fp": "0",
            },
            # Real human esports with active volume and open interest
            {
                "ticker": "KXLOLGAME-26OCT15BLGT1-BLG",
                "title": "League of Legends Worlds: BLG vs T1",
                "market_type": "binary",
                "volume_24h_fp": "5000",
                "liquidity_dollars": "2500",
                "open_interest_fp": "1200",
                "yes_bid_dollars": "0.42",
                "yes_bid_size_fp": "10",
                "yes_ask_dollars": "0.44",
                "yes_ask_size_fp": "10",
            },
            {
                "ticker": "KXCS2MAP-26SEP251015AIMINN-1-AIM",
                "title": "CS2 Map 1: AIM vs INN",
                "market_type": "binary",
                "volume_24h_fp": "3000",
                "liquidity_dollars": "1500",
                "open_interest_fp": "800",
                "yes_bid_dollars": "0.50",
                "yes_bid_size_fp": "10",
                "yes_ask_dollars": "0.52",
                "yes_ask_size_fp": "10",
            },
        ]
    }

    with patch("pm_alpha.discovery.urlopen") as urlopen, patch(
        "pm_alpha.discovery.json.load", return_value=payload
    ):
        urlopen.return_value = Response()
        disc = KalshiMarketDiscovery()._discover(limit=10)

    tickers = [m.ticker for m in disc]
    # Virtual esports with 0 volume should be filtered out
    assert "KXESOCCERGAME-26OCT15-MANCHEL" not in tickers
    assert "KXEBASKETBALLGAME-26OCT15-LALBOS" not in tickers
    # Real human esports should be selected
    assert "KXLOLGAME-26OCT15BLGT1-BLG" in tickers
    assert "KXCS2MAP-26SEP251015AIMINN-1-AIM" in tickers


def test_discovery_esports_quota_twenty_percent():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    # Create payload with ample candidates across categories
    markets = []
    # 20 weather
    for i in range(20):
        markets.append({"ticker": f"KXTEMP-{i}", "market_type": "binary", "volume": "100"})
    # 20 esports (human esports)
    for i in range(20):
        markets.append({"ticker": f"KXLOLGAME-{i}", "market_type": "binary", "volume": "100", "open_interest": "50"})
    # 20 macro
    for i in range(20):
        markets.append({"ticker": f"KXGOLD-{i}", "market_type": "binary", "volume": "100"})

    with patch("pm_alpha.discovery.urlopen") as urlopen, patch(
        "pm_alpha.discovery.json.load", return_value={"markets": markets}
    ):
        urlopen.return_value = Response()
        # Discover with limit = 20 -> 20% esports quota = 4 markets
        disc = KalshiMarketDiscovery()._discover(limit=20)

    esports_selected = [m for m in disc if m.category == "esports"]
    assert len(esports_selected) == 4  # 20 * 0.20 = 4 markets

