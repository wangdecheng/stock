# 05 — Dashboard and Stock Detail pages with MarkPoint + Grid linkage

**What to build:** Opening the app shows a dashboard with the active-strategy card, real-positions market value + top-5 table, the active strategy's virtual-book PnL curve vs benchmark, today's action-plan table, and Apply / Run-now buttons. Opening Stock Detail for any symbol shows a Pyecharts K-line overlaid with the active strategy's historical buy/sell markers, with volume + a selectable indicator (MA/BOLL/MACD/RSI) linked via Pyecharts Grid.

**Blocked by:** 04 — needs `strategy_suggestions` populated (T6's sentinel rows count) and `real_trades` for dashboard widgets.

**Status:** ready-for-agent

- [ ] `app.py` dashboard renders per `specs/spec-a-stock-quant/ui-pages.md` § Page 1: active-strategy card, real-positions aggregate card + top-5 holdings by market value (`real_position` aggregate), virtual-book PnL chart vs benchmark, today's `strategy_suggestions` with action badges, "Run now" and "Apply all to virtual book" buttons.
- [ ] Pyecharts pattern (Kline + MarkPoint + Grid + `st_pyecharts`) follows the snippets in `ui-pages.md`; no `import plotly` in the codebase.
- [ ] `pages/1_股票详情.py`: symbol search / picker (`st.selectbox`), K-line over selectable ranges (1M / 3M / 6M / 1Y / 3Y), MarkPoints color-coded by `action` (buy / sell / hold), volume + one indicator (MA / BOLL / MACD / RSI — user-pickable) sub-charts linked via Grid.
- [ ] MarkPoint query reads `strategy_suggestions` for the active strategy on the viewed symbol; empty history renders "no signals yet" placeholder rather than failing.
- [ ] Active strategy is sourced from `config.active_strategy_id`; "no active strategy" placeholder on the dashboard if empty.
- [ ] iPad-width landscape layout is unbroken (no horizontal scrollbar); phone width is acceptable-rough (per T11).
- [ ] Charts use the cached bars first; if `stale_seconds > 0`, the page shows the "数据延迟" badge inherited from the data-adapter contract.
- [ ] `st.session_state` keys (`current_strategy`, `current_symbol`) carry the cross-page state per `ui-pages.md`.

## References (read alongside this ticket)

- `specs/spec-a-stock-quant/SPEC.md` — CAP-6 + Constraints 12-13 (charts must use Pyecharts + `streamlit-echarts[pyecharts]`)
- `specs/spec-a-stock-quant/ui-pages.md` § Pages 1 + 2 — full wireframes + Pyecharts snippets + `st.session_state` keys
- `specs/spec-a-stock-quant/data-adapter.md` § Outage path — "数据延迟" badge
- `specs/spec-a-stock-quant/position-schema.md` § Real-position aggregate — derived view, not a stored table
