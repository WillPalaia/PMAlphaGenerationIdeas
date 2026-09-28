import asyncio

from pm_alpha.models import MarketSnapshot, OrderIntent, Side
from pm_alpha.paper import PaperConfig, PaperRunner
from pm_alpha.storage import SnapshotStore


class Source:
    def __init__(self):
        self.calls = 0

    async def snapshots(self):
        self.calls += 1
        return [MarketSnapshot(self.calls, "kalshi", "m1", 0.4, 0.5, 1, 1)]


class Strategy:
    def on_snapshot(self, snapshot):
        return [OrderIntent(snapshot.timestamp_ms, "kalshi", "m1", Side.BUY, 1, 0.5, f"o-{snapshot.timestamp_ms}")]


def test_paper_runner_persists_and_enforces_order_limit(tmp_path):
    intents = []

    async def record(intent):
        intents.append(intent)

    async def run():
        source = Source()
        runner = PaperRunner(
            source,
            Strategy(),
            SnapshotStore(tmp_path / "paper.sqlite"),
            PaperConfig(poll_interval_seconds=0.001, max_order_notional=0.49),
            record,
        )
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.005)
        runner.stop()
        await task
        return source

    source = asyncio.run(run())
    assert source.calls > 0
    assert intents == []
    assert SnapshotStore(tmp_path / "paper.sqlite").load("kalshi", "m1")


def test_multistrategy_basket_filtering():
    from pm_alpha.discovery import DiscoveredMarket
    from pm_alpha.paper import MultiStrategy

    class MockStrategy:
        def __init__(self, name):
            self.name = name
            self.seen = []

        def on_snapshot(self, snapshot):
            self.seen.append(snapshot.market_id)
            return [OrderIntent(snapshot.timestamp_ms, snapshot.venue, snapshot.market_id, Side.BUY, 1, 0.5, f"o-{snapshot.timestamp_ms}")]

    strat_macro = MockStrategy("macro-only")
    strat_sports = MockStrategy("sports-only")
    strat_diversified = MockStrategy("all-markets")
    strat_closing = MockStrategy("closing-only")

    baskets = {
        "macro-only": {"macro_finance"},
        "sports-only": {"sports"},
        "all-markets": {"all"},
        "closing-only": {"closing_soon"},
    }

    meta = {
        "KXGOLDH-1": DiscoveredMarket("KXGOLDH-1", "Gold", "", 10, 10, 10, category="macro_finance", hours_to_close=100.0),
        "KXNFL-1": DiscoveredMarket("KXNFL-1", "NFL", "", 10, 10, 10, category="sports", hours_to_close=10.0),
    }

    ms = MultiStrategy(
        {
            "macro-only": strat_macro,
            "sports-only": strat_sports,
            "all-markets": strat_diversified,
            "closing-only": strat_closing,
        },
        baskets=baskets,
        market_metadata=meta,
    )

    snap_macro = MarketSnapshot(1, "kalshi", "KXGOLDH-1", 0.4, 0.5, 1, 1)
    snap_sports = MarketSnapshot(2, "kalshi", "KXNFL-1", 0.4, 0.5, 1, 1)

    intents1 = ms.on_snapshot(snap_macro)
    intents2 = ms.on_snapshot(snap_sports)

    # strat_macro should only have seen KXGOLDH-1
    assert strat_macro.seen == ["KXGOLDH-1"]
    # strat_sports should only have seen KXNFL-1
    assert strat_sports.seen == ["KXNFL-1"]
    # strat_diversified should have seen both
    assert strat_diversified.seen == ["KXGOLDH-1", "KXNFL-1"]
    # strat_closing should only have seen KXNFL-1 (hours_to_close=10 <= 48)
    assert strat_closing.seen == ["KXNFL-1"]

    # Verify intent strategy tagging
    macro_strats = {i.strategy for i in intents1}
    assert macro_strats == {"macro-only", "all-markets"}

    sports_strats = {i.strategy for i in intents2}
    assert sports_strats == {"sports-only", "all-markets", "closing-only"}
