"""ADX+Bollinger Band regime strategy.

Spec reference: ``.scratch/adx-bb-regime/spec.md`` (D3 / D4 / D5 / D8 / D9).

This file implements the full four-regime StateMachine (TREND_UP /
TREND_DOWN / RANGE_BULL / RANGE_BEAR), the Hysteresis Gate from D3/D4
(a 2-day confirmation that ADX is in a TREND_UP configuration before the
strategy actually enters that state), the HalvedStage internal
trailing stop from D3 / T04, and the Phased Exit mechanic from D4 / T05.
The Range_Bull signal stack (T06) is out of scope here.

State persistence follows D5: five keys are populated on first run via
``ctx.state.setdefault(...)`` so pre-existing values (e.g. from a hand-
edited ``state.json``) are never overwritten — only ``current_state`` is
rewritten each bar because it is the *result* of classification. The
engine flushes ``ctx.state`` to disk via the existing
``framework.strategy.persistence`` helpers, so this file does not import
or call them.

Hysteresis Gate
---------------
The gate is implemented as a *data-driven* consecutive count rather than
a pure ``ctx.state``-driven one: ``_count_consecutive`` walks the
trailing bars of the indicator DataFrame and counts how many classify as
today's candidate_state. The gate fires when that count meets
``self.hysteresis_days`` AND today's close exceeds the Bollinger mid.
This avoids a ``ctx.state`` cold-start problem (pending_days would start
at 0 on first run and block a legitimate multi-bar trend in tests) and
keeps ``ctx.state["pending_state"] / "pending_days"`` from being
clobbered on user-edited state — the same property T02's
``test_state_keys_not_clobbered_on_second_run`` pins.

HalvedStage (T04)
-----------------
Inside the TREND_UP branch, ``ctx.state["trend_up_stage"]`` carries a
three-stage internal stop machine:

    full     default Close-vs-MA20 watch     weight = 1.00
    halved   Close < MA20 has fired          weight = 0.50
    cleared  Close < MA10 has fired (post-half) weight = 0.00

Re-entering TREND_UP from any other state resets ``trend_up_stage`` to
``"full"`` so a fresh trend gets the full two-stage stop again. The
Close-vs-MA breaches use **strict** ``<`` so Close == MA20 is *not* a
trigger.

Phased Exit (T05)
-----------------
D4 pins entry as *instant* (engine fills at next-day open) and exit as
*phased* — when ``current_state`` differs from yesterday's AND
yesterday's state was held (TREND_UP or RANGE_BULL — the two states
whose default weight is non-zero), the strategy walks toward the new
default over ``exit_phased_days`` bars (default 2) rather than
rebalancing in one bar.

Mechanically: ``ctx.state["exit_in_progress"]`` is either ``None`` or
a JSON-serializable ``{"target": float, "days_left": int}``. On every
bar, if the previous bar's state was held AND it differs from today's
classification, the dict is (re)written with ``target = today's natural
weight`` and ``days_left = exit_phased_days``. If the dict is present
(from this bar or an earlier one), the strategy emits

    weight = prev_weight + (target - prev_weight) / days_left

where ``prev_weight`` is yesterday's natural weight (HalvedStage-
derived for TREND_UP, 0.5 for RANGE_BULL, 0.0 otherwise). After
emitting, ``days_left`` is decremented; when it reaches 0 the dict is
cleared.

Mid-exit reversals — e.g. ``TREND_UP → RANGE_BULL → TREND_UP`` where
the second transition fires while ``exit_in_progress`` is still active —
overwrite the dict with the new destination and reset ``days_left``.
The strategy does NOT try to "undo" a partial exit.
"""

from __future__ import annotations

import pandas as pd

from framework.strategy import Context


class AdxBbRegimeStrategy:
    """ADX+Bollinger Band regime classifier.

    Each bar classifies into one of four TrendStates and emits a default
    weight per the D3 cascade. Entry to TREND_UP is gated by the
    Hysteresis Gate: the trailing ``hysteresis_days`` bars (default 2)
    must all classify as TREND_UP, and today's close must exceed the
    Bollinger mid band. All other transitions (TREND_DOWN, RANGE_BULL,
    RANGE_BEAR) fire on the bar the conditions become true.
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
        # ``rsi_len`` is reserved for T06 (Range_Bull signal stack) — it
        # is accepted now so the UI form binds cleanly without needing a
        # constructor signature change later. ``exit_phased_days`` is
        # used by T05's Phased Exit (number of bars over which to walk
        # toward a new state's default weight).
        self.adx_len = adx_len
        self.bb_len = bb_len
        self.bb_std = bb_std
        self.rsi_len = rsi_len
        self.hysteresis_days = hysteresis_days
        self.exit_phased_days = exit_phased_days

    def generate(self, ctx: Context) -> dict[str, float]:
        # D5 state schema — populate on first run, never overwrite. T02's
        # ``test_state_keys_not_clobbered_on_second_run`` requires that
        # pre-populated values (e.g. a hand-edited state.json) are kept
        # verbatim, so we ONLY use ``setdefault`` here. ``current_state``
        # is rewritten at the bottom of this method — it is the result
        # of classification, not a user input.
        ctx.state.setdefault("current_state", "RANGE_BEAR")
        ctx.state.setdefault("pending_state", None)
        ctx.state.setdefault("pending_days", 0)
        ctx.state.setdefault("trend_up_stage", "full")
        ctx.state.setdefault("exit_in_progress", None)

        # Capture the previous bar's state for T04's re-entry detection
        # AND T05's phase-out interpolation. ``ctx.state["current_state"]``
        # is the LAST bar's state (set at the bottom of this method),
        # so this is what was active before today's classification.
        prev_state = ctx.state["current_state"]
        # The natural weight the strategy was emitting on the previous
        # bar — i.e. the starting point of T05's linear ramp toward
        # ``exit_in_progress["target"]``. Captured BEFORE T04 modifies
        # ``trend_up_stage`` so it reflects yesterday's stage, not
        # today's.
        prev_weight = self._prev_state_weight(
            prev_state, ctx.state["trend_up_stage"]
        )

        # Universe is exactly one symbol per D2. ``ctx.universe[0]`` is
        # the contract.
        symbol = ctx.universe[0]

        # Pull a window large enough for ADX(14) + Wilder smoothing to
        # converge; ``lookback=120`` is well above the MA60 warmup.
        df = ctx.bars(symbol, lookback=120)

        adx_df = ctx.indicator("adx", df, length=self.adx_len)
        ma10 = ctx.indicator("ma", df, length=10)
        ma20 = ctx.indicator("ma", df, length=20)
        ma60 = ctx.indicator("ma", df, length=60)
        bb_df = ctx.indicator(
            "bollinger", df, length=self.bb_len, std=self.bb_std
        )

        # Today's bar — the last row of each indicator.
        adx_today = float(adx_df["adx"].iloc[-1])
        plus_di_today = float(adx_df["plus_di"].iloc[-1])
        minus_di_today = float(adx_df["minus_di"].iloc[-1])
        close_today = float(df["close"].iloc[-1])
        ma10_today = float(ma10.iloc[-1])
        ma20_today = float(ma20.iloc[-1])
        ma60_today = float(ma60.iloc[-1])
        bb_mid_today = float(bb_df["mid"].iloc[-1])

        # D3 cascade — today's candidate_state.
        candidate_state = self._classify(
            adx_today,
            plus_di_today,
            minus_di_today,
            close_today,
            ma20_today,
            ma60_today,
        )

        # Hysteresis Gate: count trailing consecutive bars (including
        # today) that classify as candidate_state. Capped at
        # ``hysteresis_days`` for efficiency — once we know the gate
        # is satisfied we stop walking back. The result drives TREND_UP
        # entry only; other transitions fire instantly.
        n_consecutive = self._count_consecutive(
            df,
            adx_df,
            ma20,
            ma60,
            candidate_state,
            max_count=self.hysteresis_days,
        )

        # D3 state priority Else-If cascade + Hysteresis Gate.
        if candidate_state == "TREND_UP":
            if (
                n_consecutive >= self.hysteresis_days
                and close_today > bb_mid_today
            ):
                # Hysteresis Gate fires — enter TREND_UP.
                new_state = "TREND_UP"
                weight = 1.0
            else:
                # Gate doesn't fire. TREND_UP is the candidate we wanted
                # but did not get confirmation for; the strategy falls
                # back to RANGE_BEAR (D3 default-weight-0 fallback).
                new_state = "RANGE_BEAR"
                weight = 0.0
        elif candidate_state == "TREND_DOWN":
            # Instant transition — TREND_DOWN is held-state default 0.
            new_state = "TREND_DOWN"
            weight = 0.0
        elif candidate_state == "RANGE_BULL":
            # Instant transition — RANGE_BULL default 0.5 (T06 will add
            # the BB / RSI / K-line signal stack on top).
            new_state = "RANGE_BULL"
            weight = 0.5
        else:
            # RANGE_BEAR — the explicit fallback (else branch in D3).
            new_state = "RANGE_BEAR"
            weight = 0.0

        # ----- T04: HalvedStage machine --------------------------------
        # Evaluated only when ``new_state == TREND_UP`` (we are inside
        # the held TREND_UP branch this bar). Re-entry from another
        # state resets the stage to ``"full"`` so a fresh trend gets
        # the full two-stage stop; otherwise the three-stage rule
        # transitions the stage on strict ``<`` breaches.
        if new_state == "TREND_UP":
            if prev_state != "TREND_UP":
                # Re-entry from another state — reset to full, weight
                # is the stage's default (1.0).
                ctx.state["trend_up_stage"] = "full"
                weight = 1.0
            else:
                # Stay in TREND_UP — apply the three-stage rule.
                stage = ctx.state["trend_up_stage"]
                if stage == "full":
                    if close_today < ma20_today:
                        ctx.state["trend_up_stage"] = "halved"
                        weight = 0.5
                    # else: keep "full", weight stays at default 1.0.
                elif stage == "halved":
                    if close_today < ma10_today:
                        ctx.state["trend_up_stage"] = "cleared"
                        weight = 0.0
                    else:
                        # No breach — stage stays "halved", emit its
                        # default weight of 0.5 (no double-trigger).
                        weight = 0.5
                else:  # "cleared"
                    # Fully out — weight stays at the stage's default
                    # 0.0 and stage is unchanged.
                    weight = 0.0

        # ----- T05: Phased Exit -----------------------------------------
        # D4's entry-asymmetry rule: leaving a held state is *phased*
        # (2-bar linear sell) while entering is *instant*. Held states
        # are TREND_UP and RANGE_BULL — the two states whose default
        # weight is non-zero per D3. We detect the phase-out in two
        # flavors and combine them into a single rule:
        #
        #   1. **Initial exit** (sell-down): ``prev_state`` was held,
        #      ``prev_state != new_state``, AND today's natural weight
        #      is *lower* than ``prev_weight``. We are shrinking the
        #      position — phase it out. Buy-ups (e.g. RANGE_BULL 0.5 →
        #      TREND_UP 1.0) stay instant: the new held-state entry is
        #      single-bar, no phasing.
        #
        #   2. **Mid-exit reversal** (destination change while phasing):
        #      ``prev_state`` was held, ``prev_state != new_state``, AND
        #      ``exit_in_progress`` is already active. The new held-
        #      state entry / non-held-state exit OVERWRITES the in-
        #      progress phase-out with the new destination and resets
        #      ``days_left``. Per D4 the strategy does NOT try to "undo"
        #      a partial exit — it just aims the ramp at a new target.
        #
        # The combined predicate is
        # ``prev_was_held and prev_state != new_state and
        # (prev_exit_info is not None or weight < prev_weight)`` —
        # firing on either an initial sell-down or any flip while a
        # phase-out is already running.
        held_states = {"TREND_UP", "RANGE_BULL"}
        prev_was_held = prev_state in held_states
        prev_exit_info = ctx.state["exit_in_progress"]
        if (
            prev_was_held
            and prev_state != new_state
            and (prev_exit_info is not None or weight < prev_weight)
        ):
            ctx.state["exit_in_progress"] = {
                "target": weight,
                "days_left": self.exit_phased_days,
            }

        # Apply the linear interpolation toward ``target``. We use
        # ``prev_weight`` (captured at the top, BEFORE T04 modified
        # ``trend_up_stage``) as the starting point — this is the weight
        # the strategy was emitting yesterday, which is what we are
        # ramping *from*. On the transition day ``prev_weight`` is the
        # previous held state's natural weight (1.0 for TREND_UP full,
        # 0.5 for RANGE_BULL, etc.); on subsequent days of the phase-out
        # ``prev_weight`` is whatever weight the strategy emitted
        # yesterday, and the ramp walks from there toward ``target``.
        exit_info = ctx.state["exit_in_progress"]
        if exit_info is not None:
            target = exit_info["target"]
            days_left = exit_info["days_left"]
            weight = prev_weight + (target - prev_weight) / days_left
            # Decrement ``days_left``. When it hits 0, clear the dict so
            # the next bar runs on the natural state logic alone. The
            # ``<= 1`` guard handles the case where ``exit_phased_days``
            # is configured to 1 — we still want exactly one bar of
            # interpolation, then clear.
            if days_left <= 1:
                ctx.state["exit_in_progress"] = None
            else:
                ctx.state["exit_in_progress"] = {
                    "target": target,
                    "days_left": days_left - 1,
                }

        ctx.state["current_state"] = new_state
        return {symbol: weight}

    # ----- helpers --------------------------------------------------------

    @staticmethod
    def _classify(
        adx_val: float,
        plus_di: float,
        minus_di: float,
        close: float,
        ma20: float,
        ma60: float,
    ) -> str:
        """Apply the D3 cascade to a single bar's indicator values.

        Priority order (first match wins):
            1. TREND_UP    — ADX > 25 AND +DI > -DI AND Close > MA20
            2. TREND_DOWN  — ADX > 25 AND +DI < -DI
            3. RANGE_BULL  — ADX < 20 AND Close > MA60
            4. RANGE_BEAR  — else fallback
        """
        if adx_val > 25 and plus_di > minus_di and close > ma20:
            return "TREND_UP"
        if adx_val > 25 and plus_di < minus_di:
            return "TREND_DOWN"
        if adx_val < 20 and close > ma60:
            return "RANGE_BULL"
        return "RANGE_BEAR"

    @staticmethod
    def _count_consecutive(
        df: pd.DataFrame,
        adx_df: pd.DataFrame,
        ma20: pd.Series,
        ma60: pd.Series,
        target_state: str,
        *,
        max_count: int,
    ) -> int:
        """Count trailing consecutive bars (including today) whose cascade
        classification equals ``target_state``.

        Walks back from ``iloc[-1]`` (today) toward ``iloc[0]``, applying
        the cascade to each bar's indicator slice. Stops at the first
        non-match or when ``max_count`` is reached.

        ``max_count`` should be set to ``self.hysteresis_days`` so we
        only walk as far as the gate requires — the rest of the
        history is irrelevant to the TREND_UP entry decision.
        """
        count = 1  # today always counts as 1
        offset = 2  # start at yesterday (iloc[-2])
        n = len(df)
        while count < max_count and offset <= n:
            adx_val = float(adx_df["adx"].iloc[-offset])
            plus_di = float(adx_df["plus_di"].iloc[-offset])
            minus_di = float(adx_df["minus_di"].iloc[-offset])
            close = float(df["close"].iloc[-offset])
            ma20_val = float(ma20.iloc[-offset])
            ma60_val = float(ma60.iloc[-offset])
            state = AdxBbRegimeStrategy._classify(
                adx_val,
                plus_di,
                minus_di,
                close,
                ma20_val,
                ma60_val,
            )
            if state != target_state:
                break
            count += 1
            offset += 1
        return count

    @staticmethod
    def _prev_state_weight(prev_state: str, trend_up_stage: str) -> float:
        """Natural weight the strategy was emitting on the previous bar.

        Used by T05's Phased Exit interpolation as the starting point of
        the linear ramp toward ``exit_in_progress["target"]``. The held
        states (TREND_UP, RANGE_BULL) carry non-zero natural weights;
        TREND_DOWN and RANGE_BEAR default to 0.0. For TREND_UP the
        HalvedStage adjusts the natural weight downward as the trailing
        stop fires (full=1.0, halved=0.5, cleared=0.0) — this helper
        captures that adjustment.
        """
        if prev_state == "TREND_UP":
            if trend_up_stage == "full":
                return 1.0
            if trend_up_stage == "halved":
                return 0.5
            return 0.0  # "cleared"
        if prev_state == "RANGE_BULL":
            return 0.5
        return 0.0  # TREND_DOWN or RANGE_BEAR


__all__ = ["AdxBbRegimeStrategy"]
