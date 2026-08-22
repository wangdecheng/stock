# 09 — 511010 zero-volatility regression test

**What to build:** A pytest assertion that runs the regime strategy against a near-zero-volatility series (the 511010 国债ETF data, OR a synthetic near-zero-volatility series if the real 511010 data is not yet cached locally) and confirms that the strategy's `target_weight == 0` for ≥ 99% of bars after warmup. The test fails with a diagnostic message naming each bar where a non-zero weight was emitted, so that future regressions are debuggable in one read. End-to-end demo: `pytest tests/test_regime_zero_vol_regression.py` exits 0 on the current implementation.

**Blocked by:** 07 — needs the multi-universe runner (or its underlying primitives) to run a single backtest cleanly.

**Status:** ready-for-agent

- [x] File `tests/test_regime_zero_vol_regression.py` exists.
- [x] Test runs the regime strategy against 511010 (preferred) OR against a synthetic near-zero-volatility series (constructed via `numpy.random` with `sigma ≈ 0.001` daily).
- [x] Captures the sequence of `target_weight` values emitted by `generate()`.
- [x] Asserts: `count(weight == 0) / len(weights_after_warmup) >= 0.99`.
- [x] On failure, prints a diagnostic listing the first 10 bars where `weight != 0`, with the bar date, emitted weight, `current_state`, and the indicator snapshot (`adx`, `+di`, `-di`, `close vs bb_lower`, `rsi`). This makes the failure self-explanatory.
- [x] Uses an adapter fixture consistent with existing test conventions (verify by reading `tests/test_data_layer.py` or similar).
- [x] Does NOT require live network access — works on cached data or synthetic data.
- [x] The synthetic-data fallback exists so the test is runnable even before 511010 data is cached.
- [x] Test is marked `@pytest.mark.regression` (verify whether the project already has a marker convention; if not, just use a plain test).
- [x] Test runs in < 5 seconds.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D6 (511010 role), Testing Decisions §9
- `tests/` — existing test conventions and fixtures
- `framework/data/adapter/*` — adapter fixture patterns
- Ticket 07 — runner primitives to reuse
- Ticket 06 — Range_Bull signals are the most likely source of false-positive signals on flat series; this test catches their leakage
