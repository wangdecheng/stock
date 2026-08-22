"""framework/strategy/__init__.py

Public surface for the strategy layer (T2: CAP-3, CAP-4). Strategy files
in ``strategies/*.py`` import only from here::

    from framework.strategy import Strategy, Context

Anything deeper (engine, persistence, indicators, metrics) is **forbidden**
inside strategy files; the discoverer enforces that AST-level.
"""

from __future__ import annotations

from framework.strategy.base import Position, Strategy, Trade
from framework.strategy.context import (
    Context,
    SignalError,
    UnknownIndicatorError,
    validate_signal,
)
from framework.strategy.discover import (
    DiscoverError,
    DuplicateStrategyNameError,
    ForbiddenImportError,
    discover_strategies,
)
from framework.strategy.persistence import (
    load_params,
    load_state,
    params_path,
    save_params,
    save_state,
    state_path,
)

__all__ = [
    # base
    "Strategy",
    "Position",
    "Trade",
    # context
    "Context",
    "UnknownIndicatorError",
    "SignalError",
    "validate_signal",
    # discover
    "discover_strategies",
    "DiscoverError",
    "DuplicateStrategyNameError",
    "ForbiddenImportError",
    # persistence
    "load_params",
    "save_params",
    "load_state",
    "save_state",
    "params_path",
    "state_path",
]
