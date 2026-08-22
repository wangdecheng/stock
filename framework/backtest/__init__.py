"""framework/backtest/__init__.py — public surface (T3)."""

from framework.backtest.engine import Engine, EngineConfig, Equity, Fill
from framework.backtest.metrics import compute as metrics_compute
from framework.backtest.store import has_recent_run, save_run

__all__ = [
    "Engine",
    "EngineConfig",
    "Equity",
    "Fill",
    "metrics_compute",
    "save_run",
    "has_recent_run",
]
