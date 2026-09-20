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
