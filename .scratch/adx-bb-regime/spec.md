# ADX + Bollinger Band Regime Strategy — Spec

**Status:** ready-for-agent
**Source:** outcome of a 9-decision grilling session (`/grill-with-docs`), captured 2026-08-22.
**Companion to:** `specs/spec-a-stock-quant/SPEC.md` (CAP-3, CAP-4 framework contract).

---

## Problem Statement

The user has discovered an ETF quant strategy idea online and wants to evaluate it on A-share data. The idea is intuitive — "trade the oscillation in range-bound markets, ride trends in trending markets, sit out in downtrends" — but the original write-up is conceptual: it does not specify *how* a program is supposed to define "is now a trend or a range?", nor *how* the resulting position sizes should be encoded for a daily-bar event loop.

Translating that concept into a working strategy exposes several forks that all look reasonable on paper but lead to very different backtest behaviour:

- **Universe scope**: one ETF at a time, or a basket that runs the same logic in parallel?
- **Default position**: does a range-bound market default to 0% (only buy on Bollinger touch) or 50% (treat the band as a half-position with ±50% swings)?
- **State transitions**: should a state change re-balance immediately, or in phases?
- **Trend-Up exits**: after the first MA20 break, does the remaining half get tracked by a tighter stop, hold, or get fully cleared?
- **Indicator whipsaw**: ADX bounces around its threshold — is "instant transition" defensible, or do we need 2-day confirmation?
- **RSI and K-line**: the original idea treats these as "bonus" — but does that mean *informational*, *gating*, or *position-sizing*?
- **Backtest breadth**: a single-asset backtest is easy to over-interpret; what set of ETFs does the user want to test against?

Without resolving these forks up-front, the strategy either ships with an arbitrary choice in each one (silently deciding the user's risk profile) or stalls in implementation re-litigation.

## Solution

Add a **regime-classification strategy** as a drop-in file under `strategies/`, plus a small batch-orchestration utility and a buy-and-hold baseline strategy, such that the user can run the idea against a curated basket of six ETFs and see at a glance whether the regime logic adds value over passive holding.

Concretely, the system gains:

1. **One new strategy file** that classifies each bar into one of four regimes — `TREND_UP`, `TREND_DOWN`, `RANGE_BULL`, `RANGE_BEAR` — using ADX/DI for trend strength, MA20/MA60 for trend direction, and emits a `target_position` per bar accordingly.
2. **Hysteresis on entry** to TREND_UP (2-day ADX-rising + price confirmation), so the strategy does not whipsaw on indicator noise.
3. **Phased exit** from any held state (2-day linear sell) so down-moves don't all happen on the worst bar.
4. **A two-stage trailing stop inside TREND_UP** (MA20 break → 50% off; MA10 break on the remainder → fully out).
5. **A half-position default inside RANGE_BULL** with Bollinger band touches ±RSI-modulated swing and K-line reversal-pattern alternatives.
6. **A multi-universe batch runner** that runs the same strategy code against six different universes and produces a comparison artefact.
7. **A buy-and-hold baseline strategy** of the same shape as the regime strategy, so the comparison report can answer "did the regime logic add value?".
8. **An update to the project's domain glossary** so the new concepts (StateMachine, TrendState, Hysteresis Gate, Phased Exit, HalvedStage, Signal Modulator, Adaptive Trigger) have canonical definitions before code review.

The intended outcome is not "this strategy prints money" — it is "this strategy runs end-to-end on six ETFs with transparent state behaviour, and the user can compare its results to passive holding in one read."

## User Stories

1. As an A-share ETF investor, I want to drop a single new strategy file into `strategies/` and have it appear in the UI's strategy dropdown, so that I can author quant ideas without touching framework code.
2. As the same investor, I want the strategy to automatically detect whether the current market is in a trend or a range, so that the system can pick the right execution logic for each regime.
3. As the same investor, I want the strategy to sit in cash during a confirmed downtrend, so that I do not give back gains during a bear market.
4. As the same investor, I want the strategy to keep some capital deployed in confirmed uptrends (rather than going fully in and fully out repeatedly), so that I capture the bulk of a trend move.
5. As the same investor, I want the strategy to use the existing indicator registry (ADX, Bollinger, RSI, MA) via `ctx.indicator(name, ...)`, so that adding a new indicator later does not require changes to my strategy file.
6. As the same investor, I want state (current regime, hysteresis counter, trailing-stop stage) to persist across `generate()` calls via `ctx.state`, so that the strategy resumes correctly after a restart.
7. As the same investor, I want a 2-day ADX-rising + price-above-BB-mid filter for *entering* a trend, so that the strategy is not whipsawed by a one-day ADX spike.
8. As the same investor, I want a 2-day linear *exit* from any held regime when conditions change, so that I do not sell the whole position on the worst bar of a regime change.
9. As the same investor, I want the regime-UP exit to first halve the position on an MA20 break, then track the remaining half with a tighter MA10 stop, so that one noisy close does not end a real trend.
10. As the same investor, I want the regime-BULL state to default to a half position, with Bollinger band touches adding or removing up to half of portfolio value, so that my capital is working even when no band is being touched.
11. As the same investor, I want RSI to act as a position-sizing modulator on range-bound buy signals (more extreme RSI → bigger position up to the cap), so that strong reversal signals are weighted more without blocking weaker ones.
12. As the same investor, I want K-line reversal patterns (long lower shadow, bullish engulfing) to act as alternative buy triggers inside the regime-BULL state, so that the strategy reacts to intraday reversal information, not only to close-vs-band comparisons.
13. As the same investor, I want the strategy to be runnable against multiple ETFs as separate backtests from a single CLI invocation, so that I can compare its behaviour across broad-based and sector ETFs in one sitting.
14. As the same investor, I want the comparison to include a buy-and-hold baseline for each ETF, so that I can see whether active regime logic adds value over passive ownership.
15. As the same investor, I want the basket to include one near-zero-volatility ETF (国债ETF) as a regression test, so that I can confirm the strategy does not generate spurious signals on flat series.
16. As the same investor, I want a comparison report (markdown table or similar) showing total return, annualized return, Sharpe, max drawdown for the regime strategy and the baseline across all six ETFs, so that I can identify which ETFs benefit from active management.
17. As a maintainer, I want the new domain terms (StateMachine, TrendState, Hysteresis Gate, Phased Exit, HalvedStage, Signal Modulator, Adaptive Trigger) to be defined in `CONTEXT.md` *before* the implementation tickets land, so that code review uses canonical vocabulary.
18. As a maintainer, I want the strategy to discover and run via the existing strategy-discovery protocol, so that I do not have to register it manually anywhere.
19. As a maintainer, I want the strategy's parameters (indicator lengths, hysteresis days, phased-exit days) to be exposed as `__init__` kwargs with defaults, so that the existing UI param form works without modification.
20. As a maintainer, I want the strategy to write its state to `strategies/<name>.state.json` via the existing persistence layer, so that no new persistence code is introduced.
21. As a developer implementing the strategy, I want the state-machine definition to be captured in the spec as a compact decision-rich artefact, so that I do not have to re-derive it from prose.

## Implementation Decisions

### D1. Module boundary — what is new vs reused

| Layer | Status | Notes |
|---|---|---|
| `strategies/adx_bb_regime.py` | **NEW** | The strategy file (consumes existing seams only) |
| `strategies/buy_and_hold.py` | **NEW** | Minimal baseline: `generate()` emits `{"<sym>": 1.0}` for the universe's only symbol |
| `tools/run_batch_backtest.py` | **NEW** | CLI: takes a strategy + list of universes, runs the engine once per universe, writes per-symbol reports |
| `tools/compare_to_baseline.py` | **NEW** | Reads two equity files, produces comparison table |
| `framework/strategy/*` | **REUSED** | No change — the new strategy is a Protocol implementor |
| `framework/indicators/*` | **REUSED** | `adx`, `bollinger`, `rsi`, `ma` are already registered |
| `framework/backtest/Engine` | **REUSED** | No change — multi-universe is driven from outside |
| `CONTEXT.md` | **MODIFIED** | Add 7 new domain terms (D7 below) |
| `framework/data/*` | **REUSED** | Adapter injects via `Context.adapter`; nothing new |

**Rationale.** The regime strategy is a *consumer* of existing framework seams, not an extension of them. CAP-4 already locked `Strategy.generate(ctx) -> dict` as the only seam a strategy can touch. Adding two thin CLI tools (`run_batch_backtest`, `compare_to_baseline`) is the smallest possible surface that lets us answer the research question without growing the framework.

### D2. Universe scope per backtest run

A single run uses `universe = [<one symbol>]` (i.e. exactly one ETF per backtest invocation). To compare across ETFs, the batch runner runs the same strategy code six times with six different universes. This keeps the regime logic per-symbol and avoids the basket-weighting problem (which would be a different feature).

### D3. State-machine definition

The compact decision-rich artefact (inline because prose is too lossy):

```
States                Conditions (today's bar)                          Default weight
─────────────────────────────────────────────────────────────────────────────────────────
TREND_UP              ADX > 25 AND +DI > -DI AND Close > MA20           1.00
TREND_DOWN            ADX > 25 AND +DI < -DI                            0.00
RANGE_BULL            ADX < 20 AND Close > MA60                         0.50
RANGE_BEAR            (everything else — fallback)                      0.00

State transitions:
  Enter TREND_UP   requires (ADX > 25) AND (ADX rising 2 consecutive days) AND (Close > BB mid)
                   on first bar where all three hold for 2 consecutive bars
  Exit any state   triggers phased 2-day linear sell toward new default
  Other transitions  instant on first bar where new state's conditions hold

Trend_Up internal stop (three stages):
  full     default                                       weight = 1.00
  halved   when Close < MA20 (in full or halved stage)   weight = 0.50
  cleared  when Close < MA10 (only fires in halved)      weight = 0.00

Range_Bull signal logic:
  Default                    weight = 0.50
  Sell trigger               High >= BB upper band                       weight = 0.00
  Buy trigger (any of):
    - Low <= BB lower band                              weight = 0.50 + 0.5 * clamp((30-RSI)/10, 0, 1)
    - long lower shadow  ((Close - Low) > 2 * |Close - Open|)
    - bullish engulfing   (today's body fully contains prior bar's body, today bullish)

Indicators (all via ctx.indicator allowlist):
  ADX(14)        for trend strength
  BB(20, 2)      for range execution
  RSI(14)        for buy modulation
  MA(20, 60)     for state confirmation

Warmup:
  First 60 bars (MA60 not yet valid): emit {}  (no position)
```

### D4. Entry asymmetry: instant entry, phased exit

Entering a new held state is *single-bar* (engine fills at next-day open). Leaving a held state is *two-bar linear* (target_weight moves halfway on day 1, all the way on day 2). The asymmetry reflects that on A-share ETFs the *opening price* is reached reliably (集合竞价) while the *panic low* of a sell day is not — phased exits reduce the chance of selling everything on the worst bar.

### D5. State persistence schema

`ctx.state` is the persistence surface. The strategy writes the following keys (initial values shown):

```
{
  "current_state":      "RANGE_BEAR",     # last bar's state
  "pending_state":      null,             # state being confirmed (hysteresis)
  "pending_days":       0,                # consecutive days pending_state has held
  "trend_up_stage":     "full",           # one of "full" | "halved" | "cleared"
  "exit_in_progress":   null,             # {target: float, days_left: int} when phasing out
}
```

All five keys must default to the shown values when the state file does not yet exist (first run after a fresh checkout).

### D6. Backtest basket and baseline

Six universes, each run as a separate backtest with the same strategy file:

| Code | Name | Role |
|---|---|---|
| 510300 | 沪深300ETF | low-vol broad-based |
| 510500 | 中证500ETF | mid-vol broad-based |
| 159915 | 创业板ETF | high-vol broad-based |
| 512760 | 半导体ETF | strong-trend sector |
| 512000 | 券商ETF | high-turnover sector |
| 511010 | 国债ETF | near-zero volatility — regression test |

The buy-and-hold baseline is a separate one-symbol strategy that emits `{"<sym>": 1.0}` for every bar after warmup. It exists only to feed the comparison report.

### D7. Domain vocabulary (for `CONTEXT.md`)

The following seven terms are introduced by this feature and must be added to `CONTEXT.md` *before* implementation tickets land, so that review uses canonical names:

- **StateMachine** — the 4-regime classifier; not a generic FSM.
- **TrendState** — the enumeration `{TREND_UP, TREND_DOWN, RANGE_BULL, RANGE_BEAR}`.
- **Hysteresis Gate** — the 2-day ADX-rising + price-above-BB-mid confirmation on entry to TREND_UP.
- **Phased Exit** — the 2-day linear sale when leaving any held regime.
- **HalvedStage** — Trend_Up's internal three-stage state (`full` → `halved` → `cleared`).
- **Signal Modulator** — the continuous 0-1 scalar by which RSI scales a Range_Bull buy weight.
- **Adaptive Trigger** — the multi-source buy-trigger combiner in Range_Bull (BB touch / long shadow / engulfing).

### D8. Constructor surface (for UI form binding)

`AdxBbRegimeStrategy.__init__` accepts keyword-only params with these defaults, so that the existing `__init__`-annotation-to-form widget flow renders native sliders:

| Param | Default | Notes |
|---|---|---|
| `adx_len` | 14 | ADX lookback |
| `bb_len` | 20 | Bollinger band lookback |
| `bb_std` | 2.0 | Bollinger band std multiplier |
| `rsi_len` | 14 | RSI lookback |
| `hysteresis_days` | 2 | Entry confirmation window |
| `exit_phased_days` | 2 | Phased-exit length |

### D9. Decoupling rule preservation

The new strategy file must continue to import only `Strategy` and `Context` from `framework.strategy` (CAP-3 rule, scan-time enforced). Indicators go through `ctx.indicator(...)` (allowlist), never via direct import. This is a *verification* decision, not new — but worth pinning because the new strategy touches more state than `etf_rebalance.py` and is more tempting to import internals.

## Testing Decisions

### What makes a good test for this feature

- A good test exercises behaviour that the user can *observe in the UI* — the backtest's equity curve, the comparison report's row, the regime log lines — not internal helper functions.
- Internal helpers (e.g. the RSI-modulator function, the engulfing-pattern detector) may be unit-tested in isolation, but only because they are *decision-rich* artefacts where a one-line off-by-one would be invisible in the equity curve.

### Test layers

1. **Strategy registration test.** The strategy file passes `discover_strategies` (decoupling scan) and appears under its declared `name`. Prior art: the existing tests for `etf_rebalance.py`.
2. **State-machine unit tests.** Given a fixed synthetic OHLCV series, the strategy emits the expected sequence of `current_state` values across a known window. Covers boundary cases (ADX exactly at 25, +DI exactly = -DI, MA60 just crossing).
3. **HalvedStage unit test.** A synthetic trending series with a controlled MA20/MA10 sequence produces `full → halved → cleared` transitions in the expected bars.
4. **Range_Bull signal tests.** Synthetic series with engineered Bollinger touches, long shadows, and engulfing patterns produce the expected buy/sell triggers and weight modulation by RSI.
5. **Hysteresis gate test.** Synthetic series with a one-day ADX spike above 25 surrounded by ADX=15 days does **not** enter TREND_UP; the same spike held for 2 consecutive days **does** enter.
6. **Phased-exit test.** A controlled state transition takes exactly N bars to reach the new default, with each bar's target_weight halfway closer to the new default.
7. **Multi-universe runner test.** Given 2 universes, the runner produces 2 separate backtest artefacts (Equity objects or serialized JSON), each carrying the right universe.
8. **Comparison report test.** Given two backtest runs (regime strategy + buy-and-hold) on the same universe, the comparison produces a row with both columns populated for `total_return, annualized, sharpe, max_drawdown`.
9. **Zero-volatility regression test.** On 511010 (or a synthetic near-zero-volatility series), the regime strategy emits `weight = 0` for ≥ 99% of bars after warmup; the regression test fails if any non-zero weight appears without justification.
10. **Decoupling scan.** The new strategy file passes the existing import allowlist scan unchanged.

## Out of Scope

- **Multi-symbol basket per run.** A single run uses exactly one symbol. Running N symbols in parallel with shared state is a separate feature.
- **Intraday / minute-bar execution.** The framework's MVP is daily-bar only; this strategy does not change that.
- **Adaptive parameter tuning.** `adx_len`, `bb_len`, `rsi_len`, `hysteresis_days`, `exit_phased_days` are fixed at the constructor defaults for v1. Walk-forward optimization is explicitly deferred (matches `specs/spec-a-stock-quant/SPEC.md` § Non-goals).
- **Live order routing.** Strategy produces a virtual book; the user continues to execute manually. (Already locked at SPEC § Non-goals.)
- **Cross-ETF state.** Each ETF runs as an independent strategy instance with its own `state.json`. There is no shared cross-ETF capital allocator.
- **Catastrophic-gap stop.** Daily-bar + next-day-open fill inherently accepts overnight gap risk. No gap-protection logic is added in v1.
- **In-sample / out-of-sample split.** v1 reports on the full historical window. Walk-forward / paper-trading split is deferred.
- **Custom indicator additions.** This strategy consumes the existing nine indicators only; new indicators (e.g. Keltner, Donchian) are separate features.

## Further Notes

- The 9-decision grilling that produced this spec is captured in this conversation thread (session of 2026-08-22). Decisions Q1–Q9 map to Implementation Decisions D2, D3, D4, D3 (Trend_Up stop), D3 (hysteresis), D3 (RSI modulator + K-line), D6 (basket), D6 (basket detail) — D7 (domain terms) was an emergent observation during synthesis.
- The buy-and-hold baseline is intentionally minimal. It exists to give the comparison report a non-trivial "no active management" column. A more sophisticated benchmark (e.g. an MA-crossover baseline) would be a follow-up.
- The 511010 regression test is a *free* safety net: a near-zero-volatility ETF should produce a strategy that is mostly in cash. If the regime logic ever starts generating spurious signals on flat series, this single test will catch it.
- This spec assumes the backtest engine's existing event loop (next-day-open fill, T+1 settlement, simplified slippage) is acceptable for the regime logic. The strategy does not require any engine-level changes.
- The state schema in D5 is the *minimum* set of keys the strategy needs to persist. Additional diagnostic fields (e.g. last-bar's computed ADX value for debugging) are allowed but not required.
