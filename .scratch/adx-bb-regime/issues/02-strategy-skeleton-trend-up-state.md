# 02 — Strategy skeleton + TREND_UP + state persistence

**What to build:** A drop-in strategy file `strategies/adx_bb_regime.py` that registers via the existing discovery protocol, persists state via `ctx.state` ↔ `strategies/adx_bb_regime.state.json`, and produces a runnable backtest on a single ETF (default universe `["510300"]`). The end-to-end demo: the user clicks "Refresh strategies", sees `adx_bb_regime` in the dropdown, runs a backtest on 510300, and gets an equity curve where strong-trend windows are held at 100% and non-trend windows are flat at 0%.

**Blocked by:** 01 — needs the canonical term names (TrendState etc.) for the state schema fields.

**Status:** ready-for-agent

- [x] File `strategies/adx_bb_regime.py` exists.
- [x] Class `AdxBbRegimeStrategy` declared with class attribute `name = "adx_bb_regime"`.
- [x] `__init__` accepts keyword-only params with defaults matching the spec: `adx_len=14`, `bb_len=20`, `bb_std=2.0`, `rsi_len=14`, `hysteresis_days=2`, `exit_phased_days=2`.
- [x] `generate(ctx) -> dict[str, float]` returns at most one entry: `{symbol: weight}` where `symbol = ctx.universe[0]`.
- [x] TrendState classification on today's bar emits `current_state` to `ctx.state`. For this ticket, only **TREND_UP** is implemented; the other three states are temporarily mapped to "non-trend" and emit weight `0`. The Else-If cascade is in place but only the first branch is active.
- [x] TREND_UP condition: `ADX > 25` AND `+DI > -DI` AND `Close > MA20`. Indicators come from `ctx.indicator("adx", df)` and `ctx.indicator("ma", df, length=20)` (and `length=60` later; not needed for TREND_UP only).
- [x] When `current_state == TREND_UP`, emit weight `1.0`. Otherwise emit `0.0`.
- [x] `ctx.state` initial schema populated on first run (or read from existing state.json): `current_state`, `pending_state`, `pending_days`, `trend_up_stage`, `exit_in_progress` — values documented in spec D5.
- [x] File passes `framework.strategy.discover.discover_strategies` decoupling scan (only `Strategy`, `Context`, and third-party imports).
- [x] File discoverable: `discover_strategies()["adx_bb_regime"]` returns the class.
- [ ] Backtest on `universe=["510300"]` for 2022-01-01 → latest runs to completion and emits an `Equity` whose portfolio value series is non-trivial (mostly flat, with periods at 1.0).
- [x] `strategies/adx_bb_regime.params.json` and `strategies/adx_bb_regime.state.json` are written by the persistence layer (existing `framework.strategy.persistence.save_state` etc.).
- [x] `.gitignore` continues to ignore the two strategy JSON files.
- [x] Unit test `tests/test_adx_bb_regime_02.py` exercises: (a) discovery and decoupling scan, (b) TREND_UP triggers weight 1.0 on a synthetic rising series with ADX > 25, (c) non-TREND_UP emits weight 0, (d) state file is created with all 5 default keys on first run.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D3 (state machine), D5 (state schema), D8 (constructor), D9 (decoupling)
- `specs/spec-a-stock-quant/strategy-interface.md` — full Context dataclass + decoupling rule
- `framework/strategy/__init__.py`, `framework/strategy/discover.py` — how discovery and decoupling scan work
- `framework/backtest/engine.py` — how Engine wires `generate()` calls
- `.scratch/a-stock-quant/issues/02-strategy-protocol-and-discovery.md` — prior art for a strategy ticket
