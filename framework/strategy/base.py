"""framework/strategy/base.py

The two types a strategy file may import. Per
``specs/spec-a-stock-quant/strategy-interface.md`` (CAP-3, CAP-4), strategy
modules are restricted to importing ``Strategy`` (the Protocol below) and
``Context`` (the dataclass in ``context.py``). This is the "shape" the
runtime checks for; subclasses are optional (duck-typing is enough).

``Position`` and ``Trade`` are the engine-set values a strategy reads from
its Context (see ``context.py`` for how they are populated per call).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Forward reference only — keeps this file cheap to import and prevents
    # circular import between base.py and context.py.
    from framework.strategy.context import Context


# ---------------------------------------------------------------------------
# Strategy Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Strategy(Protocol):
    """The shape every registered strategy must satisfy.

    A class is "registerable" (``framework/strategy/discover.py``) iff it has
    a class-level ``name: str`` AND an instance method ``generate(self, ctx)``.
    Subclassing ``Strategy`` is **not** required — duck-typing is enough —
    but importing it gives you type-checker help and documents intent.
    """

    name: str

    def __init__(self, **params) -> None:
        """All parameters as keyword arguments with defaults. Type annotations
        become form fields in the Streamlit UI (CAP-3)."""

    def generate(self, ctx: "Context") -> dict[str, float]:
        """Return ``{symbol: target_weight}`` for the current bar. Values are
        in ``[0, 1]``; sum must be ``<= 1``; see ``Context.validate_signal``."""


# ---------------------------------------------------------------------------
# Value types the engine sets / strategies read
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """A held lot for a single symbol in the strategy's virtual book.

    Multi-lot buys use weighted-average cost (``position-schema.md``); the
    fields below are the merged view a strategy sees at ``ctx.positions``.
    """

    symbol: str
    qty: int
    avg_cost: float
    opened_at: date


@dataclass(frozen=True)
class Trade:
    """A single execution in the strategy's trade journal.

    Backtests append one per fill; strategies read but never write.
    """

    symbol: str
    side: str          # 'buy' | 'sell'
    qty: int
    price: float
    fee: float
    executed_at: date


__all__ = ["Strategy", "Position", "Trade"]
