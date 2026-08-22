"""ADX+Bollinger Band regime strategy (T02 skeleton).

Spec reference: ``.scratch/adx-bb-regime/spec.md`` (D3 / D5 / D8 / D9).

This file implements only the TREND_UP branch of the StateMachine; the
remaining three states (TREND_DOWN / RANGE_BULL / RANGE_BEAR) are
temporarily collapsed to a single "non-trend" else-branch that emits
weight ``0``. The full 4-regime classifier, hysteresis gate, phased
exit, halved stage, and Range_Bull signal stack land in T03–T06.

State persistence follows D5: five keys are populated on first run via
``ctx.state.setdefault(...)`` so pre-existing values (e.g. from a hand-
edited ``state.json``) are never overwritten. The engine flushes
``ctx.state`` to disk via the existing ``framework.strategy.persistence``
helpers, so this file does not import or call them.
"""

from __future__ import annotations

from framework.strategy import Context


class AdxBbRegimeStrategy:
    """ADX+Bollinger Band regime classifier.

    For T02: emits weight ``1.0`` when today's bar satisfies the
    TREND_UP condition (``ADX > 25 AND +DI > -DI AND Close > MA20``),
    otherwise ``0.0``. The constructor parameters match D8 so the
    existing ``__init__``-annotation-to-form-widget flow renders native
    sliders without modification.
    """

    name = "adx_bb_regime"

    def __init__(
        self,
        *,
        adx_len: int = 14,
        bb_len: int = 20,
        bb_std: float = 2.0,
        rsi_len: int = 14,
        hysteresis_days: int = 2,
        exit_phased_days: int = 2,
    ) -> None:
        # ``bb_len``, ``bb_std``, ``rsi_len``, ``hysteresis_days``, and
        # ``exit_phased_days`` are not yet read by TREND_UP — they are
        # accepted now so the UI form binds cleanly and later tickets
        # (Range_Bull, hysteresis, phased exit) can read them without
        # changing the constructor signature.
        self.adx_len = adx_len
        self.bb_len = bb_len
        self.bb_std = bb_std
        self.rsi_len = rsi_len
        self.hysteresis_days = hysteresis_days
        self.exit_phased_days = exit_phased_days

    def generate(self, ctx: Context) -> dict[str, float]:
        # D5 state schema — populate on first run, never overwrite. The
        # engine mutates ``ctx.state`` in place and flushes to
        # ``strategies/adx_bb_regime.state.json`` via the existing
        # persistence seam (we don't touch that seam here).
        ctx.state.setdefault("current_state", "RANGE_BEAR")
        ctx.state.setdefault("pending_state", None)
        ctx.state.setdefault("pending_days", 0)
        ctx.state.setdefault("trend_up_stage", "full")
        ctx.state.setdefault("exit_in_progress", None)

        # Universe is exactly one symbol per D2. ``ctx.universe[0]`` is
        # the contract.
        symbol = ctx.universe[0]

        # Pull a window large enough for ADX(14) + Wilder smoothing to
        # converge; the default ``lookback=60`` is the bare minimum, so
        # we go to ``120`` to be safe (the comment in the ticket).
        df = ctx.bars(symbol, lookback=120)

        adx_df = ctx.indicator("adx", df, length=self.adx_len)
        ma20 = ctx.indicator("ma", df, length=20)

        # Today's bar — the last row of the indicator Series/DataFrame.
        adx_val = float(adx_df["adx"].iloc[-1])
        plus_di = float(adx_df["plus_di"].iloc[-1])
        minus_di = float(adx_df["minus_di"].iloc[-1])
        close = float(df["close"].iloc[-1])
        ma20_val = float(ma20.iloc[-1])

        # The Else-If cascade is in place but only the first branch is
        # active for this ticket — the rest land in T03 (full
        # classifier) / T04 (Trend_Up two-stage stop) / T06 (Range_Bull
        # signals). Until then, everything that is not TREND_UP emits
        # weight ``0`` and is labelled ``RANGE_BEAR`` (the fallback
        # state from D3).
        if adx_val > 25 and plus_di > minus_di and close > ma20_val:
            ctx.state["current_state"] = "TREND_UP"
            weight = 1.0
        else:
            ctx.state["current_state"] = "RANGE_BEAR"
            weight = 0.0

        return {symbol: weight}


__all__ = ["AdxBbRegimeStrategy"]
