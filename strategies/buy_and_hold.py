"""Buy-and-hold baseline strategy.

Spec reference: ``.scratch/adx-bb-regime/spec.md`` (D1, D6) and
``.scratch/adx-bb-regime/issues/08-buy-and-hold-baseline-and-comparison.md``.

The strategy is intentionally trivial — every bar after warmup emits a
single target weight of ``1.0`` for the universe's only symbol. It exists
purely as a non-active-management benchmark so the T08 comparison
report (``tools/compare_to_baseline.py``) can answer "did the regime
strategy add value over passive holding?".

D2 pins one-symbol-per-universe, so ``ctx.universe[0]`` is the contract.
The strategy holds no internal state and writes nothing to ``ctx.state``
— ``discover_strategies`` registers it purely on its ``name = "buy_and_hold"``
attribute and the decoupling scan (only ``Context`` from
``framework.strategy``).
"""

from __future__ import annotations

from framework.strategy import Context


class BuyAndHoldStrategy:
    """Trivial baseline: always target 100% in the universe's only symbol.

    Mirrors the structural shape of ``strategies/etf_rebalance.py`` so
    that ``discover_strategies`` picks it up the same way and the
    decoupling scan passes unchanged.
    """

    name = "buy_and_hold"

    def __init__(self, **kwargs) -> None:
        # The class declares no UI parameters, but ``**kwargs`` is accepted
        # so callers wiring up the strategy through the framework's
        # uniform ``StrategyCls(**params)`` entrypoint never collide on
        # unknown kwargs.
        pass

    def generate(self, ctx: Context) -> dict[str, float]:
        # Defensive empty-universe guard — the engine passes a real
        # universe for every symbol in the basket, but tests that
        # construct a Context manually (e.g. ``Context(universe=[])``)
        # should not crash with an IndexError.
        if not ctx.universe:
            return {}
        return {ctx.universe[0]: 1.0}


__all__ = ["BuyAndHoldStrategy"]
