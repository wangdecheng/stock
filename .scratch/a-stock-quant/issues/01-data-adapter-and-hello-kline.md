# 01 — Data adapter and hello K-line

**What to build:** The user opens the Streamlit app, types a stock symbol and date range, sees a Pyecharts daily K-line. With AKShare reachable, no warning is shown. With network blocked, the page still renders from the local parquet cache and shows a "数据延迟" badge.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Streamlit single-process skeleton boots with `streamlit run app.py`; page renders the symbol + date-range form and the Pyecharts K-line on submit.
- [ ] `DataAdapter` exposes `get_bars(symbol, start, end, adj, frequency)`, `get_fundamentals(symbol)`, `get_calendar(start, end)` per `specs/spec-a-stock-quant/data-adapter.md`.
- [ ] AKShare is the sole data source; no `import akshare` exists outside `framework/data/adapter.py` (verified by grep in CI).
- [ ] Burst-running 100 fetches in <60 s does not exceed 20 AKShare calls in any 60-second sliding window (rate-limit test fixture).
- [ ] On AKShare exception, `get_bars` returns the most recent parquet cache overlapping the window and sets a `stale_seconds` flag the UI reads to show "数据延迟".
- [ ] Empty AKShare response raises `EmptyBarsError` — never silently substituted with cache.
- [ ] `get_fundamentals` returns a `dict` with at least PE / PB / dividend yield keys; `get_calendar` returns a list of `date`.
- [ ] `frequency` enum maps to AKShare `period` strings (`"1"` / `"5"` / `"15"` / `"60"` for minute, `"daily"` for day bar) per the companion.
- [ ] `data/cache/<symbol>/...parquet` is gitignored.
- [ ] The `Streamlit` page consumes the cache state and renders the "数据延迟" badge when `stale_seconds > 0`.

## References (read alongside this ticket)

- `specs/spec-a-stock-quant/SPEC.md` — CAP-1 + Constraints 1/2/3 (data source / rate-limit / cache fallback)
- `specs/spec-a-stock-quant/data-adapter.md` — interface signature + AKShare method mapping + rate-limit pattern + cache semantics
- `specs/spec-a-stock-quant/ui-pages.md` — page-by-page wireframe + Pyecharts + `st_pyecharts` snippets
- `specs/spec-a-stock-quant/architecture-diagrams.md` — module map and data flow
