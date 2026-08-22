# 06 — Range_Bull multi-source signal logic (BB + RSI + K-line)

**What to build:** Inside RANGE_BULL, layer three signal sources on top of the half-position default. Bollinger band touches drive gross position shifts (lower → buy, upper → sell). RSI modulates the buy-target weight via a continuous scalar (more extreme RSI → bigger position up to the cap). K-line reversal patterns (long lower shadow, bullish engulfing) act as additional buy triggers. End-to-end demo: a synthetic bar engineered to be a Bollinger lower touch with RSI=25 produces weight `0.5 + 0.5 * (30-25)/10 = 0.75`; a bar with no signal emits weight `0.5` (the default).

**Blocked by:** 03 — needs RANGE_BULL state classification.

**Status:** done

- [x] RANGE_BULL default weight: `0.5` (overrides any RANGE_BULL logic that would emit differently).
- [x] Sell trigger: today's `high >= bb_upper` → weight `0`.
- [x] Buy triggers (any of):
  - Today's `low <= bb_lower`.
  - Long lower shadow: `(close - low) > 2 * abs(close - open)`.
  - Bullish engulfing: today's body fully contains yesterday's body AND today's `close > open`.
- [x] When a buy trigger fires, target weight = `0.5 + 0.5 * clamp((30 - rsi) / 10, 0, 1)` — the **Signal Modulator**.
- [x] `rsi` is read from `ctx.indicator("rsi", df)` (default `rsi_len=14`).
- [x] When multiple buy triggers fire on the same bar, the trigger logic still emits one weight (the modulator formula is identical across them).
- [x] If both a buy trigger and a sell trigger fire on the same bar, sell wins (priority order: sell > buy > default).
- [x] Phased-exit logic from ticket 05 still applies on top of these weights (out of scope for this ticket but must not be broken).
- [x] Backtest on 510300 still runs to completion.
- [x] Unit test `tests/test_adx_bb_regime_06.py` covers:
  - RANGE_BULL default bar (no signal): weight `0.5`.
  - BB lower touch with RSI=25: weight `0.75`.
  - BB lower touch with RSI=15: weight `1.0` (`(30-15)/10 = 1.5`, clamped to 1).
  - BB lower touch with RSI=35: weight `0.5` (`(30-35)/10 = -0.5`, clamped to 0; default applies).
  - BB upper touch: weight `0`.
  - Long lower shadow on a non-touch bar: buy trigger fires.
  - Bullish engulfing on a non-touch bar: buy trigger fires.
  - Both buy and sell on the same bar (engineered volatility bar): sell wins.
- [x] K-line pattern detectors are small private helpers (`_is_long_lower_shadow`, `_is_bullish_engulfing`) co-located in the strategy file.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D3 (Range_Bull table), Q2 + Q6 + Q7
- `CONTEXT.md` — Signal Modulator, Adaptive Trigger (after ticket 01 lands)
- Ticket 05 — phased exit must still compose
- `framework/indicators/__init__.py` — `bollinger`, `rsi` signatures
