# 03 — All 4 states + entry hysteresis (Hysteresis Gate)

**What to build:** Extend the strategy's state classifier to cover all four TrendStates (TREND_UP, TREND_DOWN, RANGE_BULL, RANGE_BEAR), and gate entry into TREND_UP with the Hysteresis Gate — a 2-day consecutive confirmation that ADX is rising AND Close is above the Bollinger mid-band. End-to-end demo: a synthetic series with a one-day ADX spike to 28 surrounded by ADX=15 days does NOT trigger TREND_UP; the same spike sustained for 2 consecutive days DOES.

**Blocked by:** 02 — needs the strategy skeleton, state persistence, and TREND_UP branch in place.

**Status:** ready-for-agent

- [x] State priority Else-If cascade is explicit in code (TREND_UP first, then TREND_DOWN, then RANGE_BULL, then RANGE_BEAR fallback).
- [x] TREND_DOWN condition: `ADX > 25` AND `+DI < -DI` → weight `0`.
- [x] RANGE_BULL condition: `ADX < 20` AND `Close > MA60` → weight `0.5` (no signals yet — that lands in ticket 06).
- [x] RANGE_BEAR is the fallback (Else branch) → weight `0`.
- [x] `current_state` is written to `ctx.state` every bar.
- [x] Hysteresis Gate logic:
  - Each bar, after computing today's raw `candidate_state`, compare to `ctx.state["pending_state"]`.
  - If equal: increment `pending_days`. If not: reset `pending_state = candidate_state`, `pending_days = 1`.
  - **Transition into TREND_UP fires** only when `pending_state == TREND_UP` AND `pending_days >= hysteresis_days` AND `Close > BB mid band`.
  - **Other transitions fire** immediately on the bar where conditions become true (no hysteresis for non-TREND_UP entry; those states are either zero-weight or RANGE_BULL with default 0.5 — neither needs anti-whipsaw protection).
- [x] After triggering a TREND_UP entry, reset `pending_state` and `pending_days` so the next entry requires a fresh confirmation cycle.
- [x] Indicator data uses `ctx.indicator("adx", df)`, `ctx.indicator("ma", df, length=20)`, `ctx.indicator("ma", df, length=60)`, `ctx.indicator("bollinger", df)` — all via the existing allowlist.
- [x] Backtest on 510300 still runs to completion.
- [x] No weight logic changes for non-TREND_UP states beyond their defaults; phased exit (ticket 05) is NOT in this ticket.
- [x] Unit test `tests/test_adx_bb_regime_03.py` covers:
  - Synthetic ADX spike to 28 surrounded by ADX=15: ticker stays out of TREND_UP.
  - ADX > 25 + rising for 2 consecutive bars + Close > BB mid: state transitions to TREND_UP on the second bar.
  - Each of the 4 states is reachable on a hand-crafted synthetic series.
  - State priority: a bar satisfying both TREND_UP and TREND_DOWN conditions (impossible in practice but a boundary case) resolves to TREND_UP per the cascade.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D3 (state machine), D4 (entry asymmetry), Q5
- `CONTEXT.md` — TrendState, Hysteresis Gate (after ticket 01 lands)
- `framework/indicators/__init__.py` — adx, ma, bollinger signatures
