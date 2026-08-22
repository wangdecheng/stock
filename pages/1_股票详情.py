"""pages/1_股票详情.py — T5 Page 2: stock detail.

Per ``ui-pages.md``:
  - Symbol picker (text input + recent-view dropdown via session_state)
  - K-line + MarkPoint buy/sell markers from past ``strategy_suggestions``
    for this symbol (overlay uses one ``MarkPoint`` call per spec)
  - Volume sub-chart via Pyecharts ``Grid``
  - Financials sidebar (PE/PB/分位/分红) via ``DataAdapter.get_fundamentals``
  - "Add to universe" placeholder button (gated by SPEC Open Question #1)

Charts: Pyecharts + ``streamlit_echarts`` ONLY (per T10).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, Kline
from streamlit_echarts import st_pyecharts

from framework.data.adapter import (
    AKShareAdapter,
    DataAdapterUnavailable,
    EmptyBarsError,
    UnknownSymbolError,
)
from framework.persistence import list_suggestions
from framework.ui_runtime import get_active_strategy_id, get_connection


# ---------------------------------------------------------------------------
# helpers (defined up-front so pyflakes sees them before use; the script
# executes top-to-bottom so hoisting is safe).
# ---------------------------------------------------------------------------


def _nearest_date_index(dates: list[str], target: date) -> Optional[int]:
    """Return the index in ``dates`` whose date is the closest to ``target``.

    Dates are ISO strings (YYYY-MM-DD). Equal-or-later wins so a suggestion
    generated after close still anchors to today's bar.
    """
    target_iso = target.isoformat() if hasattr(target, "isoformat") else str(target)
    best: Optional[int] = None
    best_delta: Optional[int] = None
    for i, d in enumerate(dates):
        delta = abs(_date_diff_days(d, target_iso))
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = i
    return best


def _date_diff_days(a: str, b: str) -> int:
    """Cheap day diff between two ISO date strings. Avoids importing
    datetime inside the loop — this runs once per historical suggestion."""
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


st.set_page_config(
    page_title="股票详情",
    page_icon="🔎",
    layout="wide",
)

st.title("🔎 股票详情")

# ----- singletons ----------------------------------------------------------


def get_adapter() -> AKShareAdapter:
    """AKShare adapter factory — cheap, skip ``@st.cache_resource`` so tests
    can monkeypatch ``app.get_adapter`` cleanly (mirrors the pattern in the
    top-level ``app.py``)."""
    return AKShareAdapter(cache_dir=Path("data/cache"))


_RANGE_OPTIONS: dict[str, int] = {
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "3Y": 365 * 3,
}

_ADJ_LABELS = {"qfq": "前复权", "hfq": "后复权", "none": "不复权"}

# session_state: keep the recently-viewed symbols around for the dropdown
if "_recent_symbols" not in st.session_state:
    st.session_state._recent_symbols: list[str] = []


# ----- inputs --------------------------------------------------------------


col1, col2, col3 = st.columns([2, 2, 1])
default_symbol = st.session_state._recent_symbols[0] if st.session_state._recent_symbols else "000001"
symbol = col1.text_input(
    "股票代码",
    value=default_symbol,
    max_chars=6,
    help="6 位 A 股代码",
).strip()
range_label = col2.selectbox(
    "时间范围",
    options=list(_RANGE_OPTIONS.keys()),
    index=3,  # 1Y
)
adj = col3.selectbox(
    "复权",
    options=list(_ADJ_LABELS.keys()),
    index=0,
    format_func=lambda x: _ADJ_LABELS[x],
)

if not symbol:
    st.info("请输入股票代码。")
    st.stop()

# record to recent-list (dedupe, keep last 10, newest first)
if symbol and symbol not in st.session_state._recent_symbols:
    st.session_state._recent_symbols.insert(0, symbol)
    st.session_state._recent_symbols = st.session_state._recent_symbols[:10]


# ----- fetch bars ----------------------------------------------------------


end = date.today()
start = end - timedelta(days=_RANGE_OPTIONS[range_label])

adapter = get_adapter()
with st.spinner("拉取 K 线…"):
    try:
        result = adapter.get_bars(symbol, start, end, adj=adj, frequency="daily")
    except EmptyBarsError:
        st.error(f"AKShare 返回空数据 ({symbol} {start}–{end}, {_ADJ_LABELS[adj]})")
        st.stop()
    except UnknownSymbolError:
        st.error(f"未知股票代码: {symbol!r}")
        st.stop()
    except DataAdapterUnavailable as exc:
        st.error(f"网络拉取失败且无缓存:{exc}")
        st.stop()

if result.stale_seconds > 0:
    st.warning(f"⚠️ 数据延迟: 来自本地缓存(共 {len(result.df)} 根 K 线)")

df: pd.DataFrame = result.df
if df.empty:
    st.info("无 K 线数据。")
    st.stop()

dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]]
ohlc = df[["open", "close", "low", "high"]].values.tolist()
volumes = df["volume"].tolist()


# ----- historical signals (MarkPoint overlay) ------------------------------


# Per ui-pages.md: "Read past strategy_suggestions for this symbol under the
# active strategy — render them as MarkPoints color-coded by action."
active_id = get_active_strategy_id()
buy_pts: list[dict] = []
sell_pts: list[dict] = []
hold_pts: list[dict] = []
if active_id is not None:
    try:
        conn = get_connection()
        history = list_suggestions(conn, active_id, include_applied=True)
    except Exception:
        history = []
    # Map date -> MarkPoint coordinate. We index by the bar date so the
    # markers land exactly on the K-line bar (not on the suggestion's
    # generated_at, which has resolution finer than the daily bar).
    date_to_idx = {d: i for i, d in enumerate(dates)}
    for s in history:
        if s.symbol != symbol:
            continue
        # Use target_price as the marker's y-value when present (price
        # precision beats the bar's high/low for visibility).
        y = s.target_price if s.target_price is not None else None
        # Find the bar date >= generated_at date.
        gen = s.generated_at.date() if hasattr(s.generated_at, "date") else s.generated_at
        # nearest date in our series
        idx = _nearest_date_index(dates, gen)
        if idx is None:
            continue
        if y is None:
            # fall back to bar close at that idx
            y = float(ohlc[idx][1])
        coord = {"coord": [dates[idx], float(y)]}
        bucket = {"buy": buy_pts, "sell": sell_pts, "hold": hold_pts}.get(s.action)
        if bucket is not None:
            bucket.append({**coord, "value": s.action})

markpoint_data = (
    [{"name": "buy", "coord": p["coord"], "value": "买", "itemStyle": opts.ItemStyleOpts(color="#ec0000")}
     for p in buy_pts]
    + [{"name": "sell", "coord": p["coord"], "value": "卖", "itemStyle": opts.ItemStyleOpts(color="#00da3c")}
        for p in sell_pts]
    + [{"name": "hold", "coord": p["coord"], "value": "持", "itemStyle": opts.ItemStyleOpts(color="#888")}
        for p in hold_pts]
)


# ----- k-line + volume (Grid) ----------------------------------------------


kline = (
    Kline(init_opts=opts.InitOpts(width="100%", height="420px"))
    .add_xaxis(dates)
    .add_yaxis(
        f"{symbol} · {_ADJ_LABELS[adj]} · 日 K",
        ohlc,
        itemstyle_opts=opts.ItemStyleOpts(
            color="#ec0000",
            color0="#00da3c",
            border_color="#8A0000",
            border_color0="#008F28",
        ),
        markpoint_opts=opts.MarkPointOpts(
            data=markpoint_data,
            symbol_size=42,
            label_opts=opts.LabelOpts(position="top", color="#fff", font_size=10),
        ) if markpoint_data else opts.MarkPointOpts(data=[]),
    )
    .set_global_opts(
        title_opts=opts.TitleOpts(title=f"{symbol}", pos_left="center"),
        xaxis_opts=opts.AxisOpts(type_="category", is_scale=True, grid_index=0),
        yaxis_opts=opts.AxisOpts(is_scale=True, grid_index=0),
        legend_opts=opts.LegendOpts(pos_top="3%"),
        tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
        datazoom_opts=[opts.DataZoomOpts(type_="inside", xaxis_index=[0, 1])],
        axispointer_opts=opts.AxisPointerOpts(
            is_show=True,
            link=[{"xAxisIndex": "all"}],
            label=opts.LabelOpts(background_color="#777"),
        ),
    )
)

# Color volumes red/green by the day's direction so the volume sub-chart
# echoes the K-line (a small but common Chinese-market convention).
# Splitting into two stacked series (up-day vol + down-day vol) is the
# idiomatic Pyecharts pattern for per-bar colors.
red_vols = [v if c >= o else 0 for v, o, c in zip(volumes, df["open"], df["close"])]
green_vols = [v if c < o else 0 for v, o, c in zip(volumes, df["open"], df["close"])]
bar = (
    Bar()
    .add_xaxis(dates)
    .add_yaxis(
        "↑",
        red_vols,
        stack="总量",
        itemstyle_opts=opts.ItemStyleOpts(color="#ec0000"),
    )
    .add_yaxis(
        "↓",
        green_vols,
        stack="总量",
        itemstyle_opts=opts.ItemStyleOpts(color="#00da3c"),
    )
    .set_global_opts(
        xaxis_opts=opts.AxisOpts(type_="category", grid_index=1),
        yaxis_opts=opts.AxisOpts(grid_index=1),
        tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
        legend_opts=opts.LegendOpts(pos_top="3%"),
    )
)

grid = (
    Grid(init_opts=opts.InitOpts(width="100%", height="600px"))
    .add(
        kline,
        grid_opts=opts.GridOpts(pos_left="8%", pos_right="4%", pos_top="8%", height="60%"),
    )
    .add(
        bar,
        grid_opts=opts.GridOpts(pos_left="8%", pos_right="4%", pos_top="72%", height="20%"),
    )
)
st_pyecharts(grid, height="600px")


# ----- financials sidebar --------------------------------------------------


with st.sidebar:
    st.subheader("财务摘要")
    try:
        fund = adapter.get_fundamentals(symbol)
    except Exception as exc:
        st.caption(f"获取失败:{exc}")
        fund = None
    if fund is not None:
        st.metric("PE", f"{fund['pe']:.2f}" if fund["pe"] is not None else "—")
        st.metric("PB", f"{fund['pb']:.2f}" if fund["pb"] is not None else "—")
        st.metric(
            "股息率",
            f"{fund['dividend_yield'] * 100:.2f}%" if fund["dividend_yield"] is not None else "—",
        )

    st.divider()
    st.caption("「添加到自选」按钮因 SPEC 开放问题 #1 暂未启用。")


# ----- "Add to universe" placeholder ---------------------------------------


st.button("添加到自选池", disabled=True, help="SPEC 开放问题 #1:等待产品决策。")