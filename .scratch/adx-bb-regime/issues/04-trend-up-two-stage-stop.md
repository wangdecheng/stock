# 04 — Trend_Up two-stage trailing stop (HalvedStage)

**What to build:** Inside the TREND_UP state, add a three-stage internal stop machine (`full → halved → cleared`). On the first Close < MA20, the position drops from 1.0 to 0.5 and the stage becomes `halved`; on the next Close < MA10, the position drops to 0 and the stage becomes `cleared`. End-to-end demo: a synthetic "rise then shallow dip then resume" sequence produces weight `1.0 → 0.5 → 0` over the right three bars, and a synthetic "rise then deep dip" produces `1.0 → 0.5 → 0` over the right two bars.

**Blocked by:** 03 — needs the full state machine and TREND_UP branch.

**Status:** ready-for-agent

- [x] `ctx.state["trend_up_stage"]` carries one of `"full" | "halved" | "cleared"`. Initial value `"full"`.
- [x] State machine update rule, evaluated **only when `current_state == TREND_UP`**:
  - If `trend_up_stage == "full"` AND `Close < MA20` → emit weight `0.5`, set `trend_up_stage = "halved"`.
  - If `trend_up_stage == "halved"` AND `Close < MA10` → emit weight `0`, set `trend_up_stage = "cleared"`.
  - If `trend_up_stage == "cleared"` → emit weight `0`, leave stage alone.
  - If neither close-below condition fires → keep stage and emit the stage's default weight (`1.0` for full, `0.5` for halved, `0.0` for cleared).
- [x] Re-entry into TREND_UP from another state resets `trend_up_stage` to `"full"` (so a fresh trend gets the full two-stage stop again).
- [x] No changes to any non-TREND_UP state in this ticket.
- [x] `ctx.indicator("ma", df, length=10)` is the MA10 source; computed alongside MA20 in the same call when possible to avoid duplicate work.
- [ ] Backtest on 510300 still runs to completion; equity curve shows earlier exits in trend-reversal regions than the ticket-02 version (which only had TREND_UP / non-TREND_UP).
- [x] Unit test `tests/test_adx_bb_regime_04.py` covers:
  - Synthetic series: 5 up bars then Close < MA20 → weight 0.5, stage halved.
  - Continuing: Close < MA10 → weight 0, stage cleared.
  - "Halved then Close recovers above MA20 but stays above MA10" → stage stays halved, weight stays 0.5 (no double-trigger).
  - Re-entry from RANGE_BULL into TREND_UP resets stage to full.
  - Boundary: Close exactly equal to MA20 → no transition (strict `<`).

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D3 (Trend_Up stop table), Q4
- `CONTEXT.md` — HalvedStage (after ticket 01 lands)
- `framework/indicators/__init__.py` — `ma` signature
