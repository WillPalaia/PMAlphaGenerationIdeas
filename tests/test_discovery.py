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
