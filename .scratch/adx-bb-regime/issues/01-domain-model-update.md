# 01 — 领域词汇入库（CONTEXT.md）

**What to build:** Before any implementation ticket lands, the seven new domain terms introduced by this feature (StateMachine, TrendState, Hysteresis Gate, Phased Exit, HalvedStage, Signal Modulator, Adaptive Trigger) must be defined in `CONTEXT.md` using the existing glossary format, so that code review on later tickets uses canonical vocabulary instead of inventing names per-PR.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Read `CONTEXT.md` to absorb the existing glossary style (Language header, term block, _Avoid_ line).
- [ ] Add a new top-level section **Strategy Layer** (or equivalent) after the existing sections.
- [ ] For each of the seven terms, write a term block matching the existing format: a one-line definition in bold, followed by an `_Avoid_:` line listing non-canonical synonyms the project should not use.
- [ ] The seven terms cover:
  - **StateMachine** — this strategy's 4-regime classifier; not a generic FSM.
  - **TrendState** — the enumeration `{TREND_UP, TREND_DOWN, RANGE_BULL, RANGE_BEAR}`.
  - **Hysteresis Gate** — the 2-day ADX-rising + price-above-BB-mid confirmation on entry to TREND_UP.
  - **Phased Exit** — the 2-day linear sale when leaving any held regime.
  - **HalvedStage** — Trend_Up's internal three-stage state (`full` → `halved` → `cleared`).
  - **Signal Modulator** — the continuous 0–1 scalar by which RSI scales a Range_Bull buy weight.
  - **Adaptive Trigger** — the multi-source buy-trigger combiner in Range_Bull (BB touch / long shadow / engulfing).
- [ ] No file other than `CONTEXT.md` is touched.
- [ ] Existing `CONTEXT.md` content is unchanged; the new section is purely additive.
- [ ] Run any existing glossary-related test (if present) to confirm no regression.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D7 (Domain vocabulary section)
- `CONTEXT.md` — existing glossary format and conventions
