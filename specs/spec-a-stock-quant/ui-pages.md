# Companion — UI Pages

This companion is part of `SPEC-a-stock-quant` (CAP-6, CAP-7, CAP-8). Locked in T5 + T10 + T11.

## Streamlit multipage layout

```
app.py                          ← Page 1: 仪表盘
pages/
  1_股票详情.py                 ← Page 2: stock detail
  2_策略管理.py                 ← Page 3: strategy admin
  3_持仓管理.py                 ← Page 4: positions
  4_回测.py                     ← Page 5: backtest
```

- File-name digit prefix controls order.
- No other pages in MVP.
- Streamlit ≥ 1.30 required for native multipage.

## Page-by-page wireframe

### Page 1 · 仪表盘 (`app.py`)

Components (Streamlit elements in parens):

- Header banner: today's date + currently-active strategy name (`st.title` + `st.caption`)
- Active-strategy card:
  - Last run timestamp, run status (success / failed / stale > 24h)
  - Link "查看完整建议 →" (navigates to strategy page; `st.page_link` in Streamlit ≥ 1.32)
- Real position card:
  - Market value, today's P&L (from `real_trades` aggregate × today's close)
  - Top-5 holdings by market value (`st.dataframe`)
- Virtual book chart:
  - Portfolio value curve vs benchmark (Pyecharts line)
  - Toggle: real / virtual / both
- Today's action plan:
  - Filtered `strategy_suggestions` for today, action badges (`st.dataframe`)
  - One-click "Apply all to virtual book" button (writes via atomic batch in `position-schema.md`)
- Run-now button:
  - Triggers the active strategy's `generate()` on click; guards on "are you sure"

### Page 2 · 股票详情 (`pages/1_股票详情.py`)

- Search/symbol picker (`st.selectbox` over a recently-viewed list)
- K-line (Pyecharts `Kline` + `MarkPoint`) over selectable range (1M / 3M / 6M / 1Y / 3Y)
- Sub-chart linkage: Volume + selectable indicator (MA / BOLL / MACD / RSI) via Pyecharts `Grid` (one call)
- Financials sidebar (PE/PB/分位/分红) — fetched via `DataAdapter.get_fundamentals`
- "Historical signals" overlay:
  - Read past `strategy_suggestions` for this symbol under the active strategy
  - Render them as MarkPoints color-coded by `action`
- "Add to universe" — placeholder button (gated by Open Question #1)

### Page 3 · 策略管理 (`pages/2_策略管理.py`)

- Strategy list (discovered by `discover_strategies()`)
  - For each: name, file path, currently-persisted params (from `params.json`), state-file size
  - Row actions: "激活", "重置 state", "删除 params.json", "在编辑器中查看" (opens file path)
- Selected strategy panel:
  - Param form auto-rendered from `__init__` annotations
  - Save → write `params.json` atomically
  - "Run now" → backtest with default window
  - Backtest history table (`backtests` rows for this strategy)
- Activation rule visualization: "需要 30 天内有一次回测" badge if blocked

### Page 4 · 持仓管理 (`pages/3_持仓管理.py`)

- Tabs: "真实持仓" | "虚拟账本"
- Real-positions tab:
  - `st.data_editor` against `real_trades` (insert / edit / delete rows)
  - "新增成交" button → form for symbol / side / qty / price / fee / date / note
  - Aggregated holdings panel (sum by symbol, avg cost, market value)
- Virtual-book tab:
  - Strategy dropdown (lists `virtual_books` keys)
  - Initial cash editor (writes `virtual_books.initial_cash`)
  - Positions table (read-only)
  - Cash & top-up / withdraw (sub-form)
  - Suggestions audit (`strategy_suggestions` for this strategy, applied/applied_unapplied filter)

### Page 5 · 回测 (`pages/4_回测.py`)

- Inputs:
  - Strategy (dropdown)
  - Date range (`st.date_input`, two inputs)
  - Initial cash (`st.number_input`, default 100 000)
- "Run" button → progress bar + result panel
- Result panel:
  - PnL curve (Pyecharts line, portfolio vs benchmark)
  - Six metric cards (`st.metric`, label + delta vs prior run if available)
  - Fills table (sortable)
  - "Save to history" — implicit; the run writes to `backtests`

## Charts — required pattern

Pyecharts + `streamlit-echarts[pyecharts]` ONLY (T10). Do not import Plotly. Do not write raw JS.

```python
from streamlit_echarts import st_pyecharts
from pyecharts.charts import Kline, Grid
from pyecharts import options as opts

kline = (
    Kline()
    .add_xaxis(dates)
    .add_yaxis("日K", ohlc)
    .set_global_opts(title_opts=opts.TitleOpts(title=symbol))
)
# add MarkPoints for buys / sells
kline.add_annotation(...)
st_pyecharts(kline, height="500px")
```

For Grid linkage (volume + indicator):

```python
grid = (
    Grid()
    .add(kline, grid_opts=opts.GridOpts(pos_left="10%", pos_right="8%", height="60%"))
    .add(volume, grid_opts=opts.GridOpts(pos_left="10%", pos_right="8%", pos_top="70%", height="20%"))
    .add(indicator_chart, grid_opts=opts.GridOpts(pos_left="10%", pos_right="8%", pos_top="92%", height="8%"))
)
st_pyecharts(grid, height="700px")
```

## State that lives in `st.session_state`

- `current_strategy`: the strategy the dashboard focuses on (mirror of "active")
- `current_symbol`: stock-detail page's currently viewed symbol
- `current_book_strategy`: positions page's currently viewed virtual book
- `last_run_id`: most recent backtest id (for "compare with prior" metric cards)

Anything page-persistent (not cross-page) is component-local. No global cache objects outside `st.session_state` and `@st.cache_data` for the data layer.

## Browser / responsive constraints (T11)

- Layout target: iPad-width landscape primary.
- Phone width: read-only acceptable; do not block, do not optimize.
- Chrome desktop is the primary debug environment.

## Auth model

Streamlit process is **unauthenticated**. Reverse proxy (Nginx/Caddy) handles basic auth + HTTPS. The Streamlit app must not branch on auth state — it always trusts the caller.
