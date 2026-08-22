# 05 — Phased Exit (2-day linear sell on state transition)

**What to build:** When the strategy's `current_state` differs from yesterday's `current_state` AND yesterday's state was a held state (TREND_UP, or RANGE_BULL with non-zero weight), exit to the new state's default via a 2-day linear sell rather than an instant rebalance. The end-to-end demo: a controlled state transition (e.g. RANGE_BULL → TREND_DOWN) takes exactly 2 bars to reach weight `0`, with the first bar's weight at the midpoint and the second bar's weight at the destination.

**Blocked by:** 03 — needs the full state machine so transitions can be detected.

**Status:** ready-for-agent

- [x] `ctx.state["exit_in_progress"]` is either `None` or a dict `{"target": float, "days_left": int}`. Initial value `None`.
- [x] On every bar, BEFORE computing today's weight:
  - If `ctx.state["current_state"]` (yesterday's value) ≠ today's computed `current_state`, AND yesterday's state was held (TREND_UP or RANGE_BULL), set `exit_in_progress = {"target": <new_state_default>, "days_left": 2}`.
- [x] If `exit_in_progress is not None`:
  - Compute `current_weight` from today's natural state logic (state's default or its signal-modified weight).
  - Emit `weight = current_weight + (target - current_weight) / days_left` (linear interpolation toward `target`).
  - Decrement `days_left`. When it reaches `0`, set `exit_in_progress = None`.
- [x] A reverse state transition (e.g. halfway through phased exit, state flips back to original) **overwrites** `exit_in_progress` with the new destination and reset `days_left = 2`. The strategy does not try to "undo" a partial exit.
- [x] If today's natural weight already equals `target` (or no exit is in progress), no phasing applies.
- [x] Backtest on 510300 still runs to completion.
- [x] Unit test `tests/test_adx_bb_regime_05.py` covers:
  - Synthetic state transition TREND_UP (1.0) → TREND_DOWN (0.0): two consecutive bars with weights `0.5`, then `0.0`; `exit_in_progress` cleared after the second bar.
  - Transition TREND_UP → RANGE_BULL: two bars `0.75`, then `0.5`.
  - Mid-exit reversal: state flips back after one bar of phasing — `exit_in_progress` overwritten with new target and `days_left = 2`.
  - Boundary: state transition to a state with the same default weight (e.g. RANGE_BULL → RANGE_BULL with default 0.5 both ways) — `exit_in_progress` is still set, but the linear interpolation is a no-op.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D4 (entry asymmetry), Q3-C
- `CONTEXT.md` — Phased Exit (after ticket 01 lands)
- Ticket 03 — state machine prerequisite
