# 02 — Strategy protocol, context, discovery, indicators, params + state persistence

**What to build:** The user drops a new `strategies/my_strat.py` defining a `Strategy` subclass with annotated `__init__` parameters; after clicking "Refresh strategies" the Strategy Management page lists it with a form whose widgets match the type annotations; saving the form writes to `strategies/my_strat.params.json` atomically. A run that mutates `ctx.state` persists across restarts via `state.json`.

**Blocked by:** 01 — needs project structure + dependency surface from the data-adapter ticket.

**Status:** ready-for-agent

- [ ] `Strategy` base class and `Context` dataclass match `specs/spec-a-stock-quant/strategy-interface.md` (now, positions, cash, portfolio_value, trades, state, bars(symbol, lookback), price(symbol), indicator(name, ...) methods).
- [ ] `discover_strategies()` scans `strategies/*.py`, registers classes with class attr `name: str` + method `generate(self, ctx) -> dict[str, float]`, and rejects modules whose source references any name outside the allowlist (`Strategy`, `Context`, plus third-party / stdlib).
- [ ] An example `strategies/ma_cross.py` is registered; the Strategy page lists it.
- [ ] `__init__(self, short: int = 5, long: int = 20)` renders a `st.slider` for `short` (5-50) and a `st.slider` for `long` (10-100); saving writes `params.json` atomically (`.tmp` + rename), `applied` shows in the UI.
- [ ] `ctx.state["x"] = 1` followed by service "save state" round-trips through `strategies/<name>.state.json` (read once at startup, written debounced on each `generate()`).
- [ ] `indicators/` module exposes `ma`, `ema`, `macd`, `bollinger`, `rsi`, `atr`, `adx`, `kdj`, `obv` with the signatures in `specs/spec-a-stock-quant/indicators-and-metrics.md`; each accept AKShare-format DataFrame directly.
- [ ] `metrics/` exposes only `compute(equity, *, risk_free=0.02) -> dict` returning the six keys: `total_return, annualized, sharpe, max_drawdown, calmar, win_rate, profit_loss_ratio`.
- [ ] Unit tests: `test_indicators_match_pandas_ta_reference.py` and `test_metrics_match_empyrical_reference.py` pass on synthetic inputs (1e-9 / 1e-6 tolerance respectively).
- [ ] `pages/2_策略管理.py` exists with: strategy list + per-strategy params form + Save button + "重置 state" + "运行" button (no backtest yet — just instantiates and runs in-process for now).
- [ ] File `strategies/<name>.params.json` and `strategies/<name>.state.json` are gitignored.

## References (read alongside this ticket)

- `specs/spec-a-stock-quant/SPEC.md` — CAP-3, CAP-4 + Constraints 4-6 (decoupling / target_position / no framework imports)
- `specs/spec-a-stock-quant/strategy-interface.md` — full base class + Context + discover protocol + params/state persistence
- `specs/spec-a-stock-quant/indicators-and-metrics.md` — required function signatures + tests
- `specs/spec-a-stock-quant/ui-pages.md` § Page 3 — Strategy Management wireframe
