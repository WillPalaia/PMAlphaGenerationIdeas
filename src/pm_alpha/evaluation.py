from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import MarketSnapshot


@dataclass(frozen=True)
class WalkForwardWindow:
    train: tuple[MarketSnapshot, ...]
    test: tuple[MarketSnapshot, ...]


def walk_forward(
    snapshots: Iterable[MarketSnapshot],
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
) -> tuple[WalkForwardWindow, ...]:
    """Create chronological, non-overlapping test windows."""

    ordered = tuple(sorted(snapshots, key=lambda item: item.timestamp_ms))
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    step = step_size or test_size
    if step <= 0:
        raise ValueError("step_size must be positive")
    windows: list[WalkForwardWindow] = []
    start = 0
    while start + train_size + test_size <= len(ordered):
        train_end = start + train_size
        test_end = train_end + test_size
        windows.append(WalkForwardWindow(ordered[start:train_end], ordered[train_end:test_end]))
        start += step
    return tuple(windows)
