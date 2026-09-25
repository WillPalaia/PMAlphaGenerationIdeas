"""Prediction-market alpha research primitives."""

from .backtest import BacktestResult, EventDrivenBacktester
from .models import MarketSnapshot, OrderIntent, Side
from .strategies import ComplementOpportunity, find_complement_opportunity
from .storage import SnapshotStore
from .paper import PaperConfig, PaperRunner
from .kalshi_source import KalshiPublicSource
from .evaluation import WalkForwardWindow, walk_forward
from .paired import PairedExecutionResult, simulate_paired_execution
from .historical import fetch_kalshi_candlesticks, load_snapshots_csv
from .metrics import BacktestMetrics, summarize
from .strategies import BuyBelowThreshold, MeanReversionStrategy, MomentumStrategy
from .experiments import ExperimentResult, compare_strategies
from .sweep import SweepRow, run_sweep, write_csv
from .market_making import (
    MarketMakerConfig,
    MarketMakerResult,
    ReferencePrice,
    simulate_reference_market_maker,
)
from .discovery import DiscoveredMarket, KalshiMarketDiscovery
from .report import MarketReport, inspect_database
from .strategies import StableHighProbabilityStrategy

__all__ = [
    "BacktestResult",
    "EventDrivenBacktester",
    "MarketSnapshot",
    "OrderIntent",
    "Side",
    "ComplementOpportunity",
    "find_complement_opportunity",
    "SnapshotStore",
    "PaperConfig",
    "PaperRunner",
    "KalshiPublicSource",
    "WalkForwardWindow",
    "walk_forward",
    "PairedExecutionResult",
    "simulate_paired_execution",
    "fetch_kalshi_candlesticks",
    "load_snapshots_csv",
    "BacktestMetrics",
    "summarize",
    "BuyBelowThreshold",
    "MeanReversionStrategy",
    "MomentumStrategy",
    "ExperimentResult",
    "compare_strategies",
    "SweepRow",
    "run_sweep",
    "write_csv",
    "MarketMakerConfig",
    "MarketMakerResult",
    "ReferencePrice",
    "simulate_reference_market_maker",
    "DiscoveredMarket",
    "KalshiMarketDiscovery",
    "MarketReport",
    "inspect_database",
    "StableHighProbabilityStrategy",
]
