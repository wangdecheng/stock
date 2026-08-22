"""ETF target-weight rebalance (CAP-4 canonical example).

Reference strategy from ``specs/spec-a-stock-quant/strategy-interface.md``:

    "An example 'ETF 目标权重再平衡' strategy returning
     ``{'510300': 0.4, '513500': 0.3, '511010': 0.3}``"

The weights are read from the strategy's ``state`` dict so a user can
re-balance via the dashboard without editing this file.
"""

from __future__ import annotations

from framework.strategy import Context


class ETFRebalanceStrategy:
    """Buy-and-rebalance to fixed weights. Universe + weights are read from
    ``ctx.state`` so the dashboard can edit them without code changes."""

    name = "etf_rebalance"

    # Class-level fallback for the runner's universe (CAP-7). Operators
    # may override via ``strategies/etf_rebalance.state.json``'s
    # ``universe`` key (read by ``framework.runner.scheduled_run``).
    default_universe: list[str] = ["510300", "513500", "511010"]

    def __init__(
        self,
        initial_cash: float = 100_000.0,
    ) -> None:
        # ``initial_cash`` is a UI parameter; ``ctx.state`` holds the
        # user-edited weights, which is the only mutable runtime config.
        self.initial_cash = initial_cash

    def generate(self, ctx: Context) -> dict[str, float]:
        # Default weights from the SPEC; user can override in state.json.
        weights: dict[str, float] = ctx.state.get("weights", {
            "510300": 0.4,   # 沪深300ETF
            "513500": 0.3,   # 标普500ETF
            "511010": 0.3,   # 国债ETF
        })
        # Constrain to symbols the engine is willing to fill.
        return {s: w for s, w in weights.items() if s in ctx.universe and w > 0}


__all__ = ["ETFRebalanceStrategy"]

