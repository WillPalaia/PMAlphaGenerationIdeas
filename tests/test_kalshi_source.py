from unittest.mock import patch
import pytest
from urllib.error import HTTPError

from pm_alpha.kalshi_source import KalshiPublicSource


def test_kalshi_source_normalizes_yes_and_no_bids():
    payload = {"orderbook": {"yes": [[40, 3], [35, 2]], "no": [[55, 4]]}}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b""

    with patch("pm_alpha.kalshi_source.urlopen") as urlopen:
        response = Response()
        response.__class__.json = staticmethod(lambda: payload)
        urlopen.return_value = response
        with patch("pm_alpha.kalshi_source.json.load", return_value=payload):
            snapshot = KalshiPublicSource(["TICKER"])._fetch("TICKER")

    assert snapshot.yes_bid == 0.40
    assert snapshot.yes_ask == pytest.approx(0.45)
    assert snapshot.bid_size == 3
    assert snapshot.ask_size == 4


def test_kalshi_source_supports_current_fixed_point_schema():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [{"price_dollars": "0.40", "quantity": "3"}],
            "no_dollars": [{"price_dollars": "0.55", "quantity": "4"}],
        }
    }
    with patch("pm_alpha.kalshi_source.urlopen") as urlopen:
        with patch("pm_alpha.kalshi_source.json.load", return_value=payload):
            snapshot = KalshiPublicSource(["TICKER"])._fetch("TICKER")
    assert snapshot.yes_bid == 0.40
    assert snapshot.yes_ask == pytest.approx(0.45)
    assert snapshot.bid_size == 3
    assert snapshot.ask_size == 4


def test_kalshi_source_retries_rate_limits():
    payload = {"orderbook": {"yes": [[40, 1]], "no": [[55, 1]]}}
    response = type("Response", (), {
        "__enter__": lambda self: self,
        "__exit__": lambda self, *args: None,
    })()
    errors = iter([
        HTTPError("url", 429, "rate limited", {}, None),
        response,
    ])
    with patch("pm_alpha.kalshi_source.urlopen", side_effect=lambda *args, **kwargs: next(errors)):
        with patch("pm_alpha.kalshi_source.json.load", return_value=payload):
            with patch("pm_alpha.kalshi_source.time.sleep"):
                snapshot = KalshiPublicSource(["TICKER"])._fetch("TICKER")
    assert snapshot.yes_bid == 0.40
