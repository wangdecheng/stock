"""framework/strategy/context.py

The ``Context`` dataclass strategies receive on every ``generate()`` call.
Locks in the shape from
``specs/spec-a-stock-quant/strategy-interface.md`` — full list of fields,
``bars()``, ``price()``, ``indicator()``, plus a free ``state`` dict the
framework loads from / writes to ``strategies/<name>.state.json``.

Decoupling: ``Context`` *receives* a data adapter via dependency injection
(in production: ``framework.data.adapter.AKShareAdapter``; in tests: a
fake). Strategies never construct a Context themselves — the engine does
that before each ``generate()`` call. Strategy files must not import this
module; they receive Context as a parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from framework.indicators import compute as _indicators_compute


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class UnknownIndicatorError(KeyError):
    """Raised when ``ctx.indicator(name, ...)`` is called with a name that is
    not in the indicators registry. Subclass of ``KeyError`` so legacy
    ``except KeyError`` catches still work, but the message names the
    missing indicator and the allowed list (silent typos are forbidden per
    CAP-3 / indicators-and-metrics.md)."""


class SignalError(ValueError):
    """Raised by ``validate_signal`` when a strategy's target_weight output
    violates the contract (out-of-range, outside universe, sums > 1)."""


# ---------------------------------------------------------------------------
# Context dataclass
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Per-call view passed into ``Strategy.generate(ctx)``.

    Field semantics
    ---------------
    ``now``
        The bar date the strategy is reasoning about. Bars fetched before
        ``now``; fills happen at next-day open (T3 ``backtest-engine.md``).
    ``universe``
        Symbols the strategy is allowed to mention in its signal. A target
        weight for a symbol outside this list is an error, not silent drop.
    ``adapter``
        Anything with a ``get_bars(symbol, start, end, **kwargs)`` method
        returning an object with a ``.df`` attribute. Injected so tests
        can swap it for a fake without monkey-patching imports.
    ``positions`` / ``cash`` / ``portfolio_value``
        The strategy's *virtual* book as-of end of the previous bar. Engine
        updates these after each fill.
    ``trades``
        Full trade journal of the virtual book. Read-only from a strategy.
    ``state``
        Free-form mutable dict. Framework loads it from
        ``strategies/<name>.state.json`` at startup and writes back
        periodically; strategies read/write freely.
    """

    now: date
    universe: list[str]
    adapter: Any = None  # data source; injected
    positions: dict = field(default_factory=dict)        # symbol -> Position
    cash: float = 0.0
    portfolio_value: float = 0.0
    trades: list = field(default_factory=list)          # list[Trade]
    state: dict = field(default_factory=dict)

    # ----- data access ---------------------------------------------------

    def bars(self, symbol: str, lookback: int = 60) -> pd.DataFrame:
        """Daily OHLCV+adj for ``symbol`` covering ``lookback`` days ending
        at ``self.now`` (inclusive). Cached for the bar by the engine if it
        needs to call repeatedly — at the Context level we just round-trip
        to the adapter on every call to keep semantics obvious.
        """
        if self.adapter is None:
            raise RuntimeError(
                "Context.adapter is None — engine must inject a data adapter"
            )
        end = self.now
        start = end - timedelta(days=lookback)
        # `lookback` is already encoded into `start` above; no need to forward
        # it to the adapter (whose contract is `(symbol, start, end, **kwargs)`
        # but whose concrete `AKShareAdapter` signature doesn't accept it).
        result = self.adapter.get_bars(
            symbol,
            start,
            end,
            adj="qfq",
            frequency="daily",
        )
        # Adapter returns BarsResult (or any object exposing .df).
        return result.df

    def price(self, symbol: str) -> float:
        """Latest close for ``symbol`` — convenience over
        ``self.bars(symbol).iloc[-1].close``."""
        df = self.bars(symbol, lookback=1)
        if df.empty:
            raise ValueError(f"no bars returned for {symbol} on {self.now}")
        return float(df.iloc[-1]["close"])

    # ----- recommendations ----------------------------------------------

    def indicator(self, name: str, *args, **kwargs):
        """Thin wrapper over ``framework.indicators``.

        ``name`` must be in the registered allowlist (CAP-3: prevents silent
        typos). ``*args / **kwargs`` are forwarded as-is to the underlying
        ``pandas-ta`` wrapper — convention is ``df`` first, then keyword
        params (``length=20`` etc.).
        """
        try:
            return _indicators_compute(name, *args, **kwargs)
        except KeyError as exc:
            allowed = ", ".join(_indicators_compute.__globals__["_REGISTRY"].keys()) or "(empty)"
            raise UnknownIndicatorError(
                f"unknown indicator {name!r}; allowed: {allowed}"
            ) from exc


# ---------------------------------------------------------------------------
# Signal validation helper
# ---------------------------------------------------------------------------


def validate_signal(signal: dict[str, float], universe: list[str]) -> None:
    """Sanity-check a strategy's ``target_position`` output.

    Rules enforced (per strategy-interface.md §"Signal contract"):

    * ``sum(v) <= 1`` — no leverage in MVP (CAP-4)
    * each ``v in [0, 1]`` — target weights are 0–1 percentages
    * each ``k`` is in ``universe`` — typos outside the universe surface to UI

    Empty dict (``{}``) is valid — engine interprets as "no rebalance".
    """
    if not isinstance(signal, dict):
        raise SignalError(f"signal must be a dict, got {type(signal).__name__}")

    universe_set = set(universe)
    bad_symbols = set(signal) - universe_set
    if bad_symbols:
        raise SignalError(
            f"signal has symbols outside the configured universe: {sorted(bad_symbols)}"
        )

    total = 0.0
    for symbol, weight in signal.items():
        if not isinstance(weight, (int, float)):
            raise SignalError(
                f"weight for {symbol!r} must be a number, got {type(weight).__name__}"
            )
        if weight < 0:
            raise SignalError(f"weight for {symbol!r} is negative ({weight})")
        if weight > 1:
            raise SignalError(f"weight for {symbol!r} exceeds 1.0 ({weight})")
        total += weight

    if total > 1.0 + 1e-9:
        raise SignalError(f"sum of weights is {total:.4f} which exceeds 1.0")


__all__ = ["Context", "UnknownIndicatorError", "SignalError", "validate_signal"]
