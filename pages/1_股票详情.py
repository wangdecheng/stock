"""pages/1_股票详情.py — T5 Page 2: stock detail.

Per ``ui-pages.md``:
  - Symbol picker (text input + recent-view dropdown via session_state)
  - K-line + MarkPoint buy/sell markers from past ``strategy_suggestions``
    for this symbol, rendered via ``framework.charts.mark_point_data``
  - Volume sub-chart via Pyecharts ``Grid`` (built by ``framework.charts``)
  - Financials sidebar (PE/PB/分位/分红) via ``DataAdapter.get_fundamentals``
  - "Add to universe" placeholder button (gated by SPEC Open Question #1)

Charts: Pyecharts + ``streamlit_echarts`` ONLY (per T10). The chart
construction itself lives in ``framework.charts`` — this page is only
responsible for wiring signals to it.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from streamlit_echarts import st_pyecharts

from framework.charts import MarkPoint, build_kline_volume_grid
from framework.data.adapter import (
    AKShareAdapter,
    BarsResult,
    DataAdapterUnavailable,
    EmptyBarsError,
    UnknownSymbolError,
)
from framework.data.ratelimit import RateLimitExceeded
from framework.persistence import list_suggestions
from framework.ui_runtime import get_active_strategy_id, get_connection

# NOTE: get_fundamentals is intentionally not called from this page. PE/PB/股息率
# were removed (user feedback: not needed; sidebar data sources were unreliable).
# The adapter method is kept on AKShareAdapter for future use.


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


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_bars_cached(symbol: str, start_iso: str, end_iso: str, adj: str) -> dict:
    """Thin serializable wrapper around ``get_bars`` so Streamlit's hash-based
    cache can memoise results across reruns.

    Caches the *BarsResult* dict (df → records + source/stale fields) keyed
    on ``(symbol, start, end, adj)``. The 60 s TTL is shorter than the
    upstream rate-limit window (20 req/min) so the user effectively gets
    at most one eastmoney round-trip per minute per (symbol, range, adj)
    tuple — which is what kept the spinner pinned at 60 s+ before this
    cache existed (every rerun repeated the full network + limiter path).
    """
    adapter = AKShareAdapter(cache_dir=Path("data/cache"))
    r: BarsResult = adapter.get_bars(
        symbol, date.fromisoformat(start_iso), date.fromisoformat(end_iso),
        adj=adj, frequency="daily",
    )
    return {
        "records": r.df.assign(date=r.df["date"].astype(str)).to_dict(orient="records"),
        "stale_seconds": int(r.stale_seconds),
        "source": r.source,
    }


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
#
# Inputs are inside an ``st.form`` so the user must explicitly click
# "查询" (which is the only action that triggers a rerun). Without this,
# the bare ``text_input`` was firing a rerun on every keystroke, each of
# which ran the full fetch + limiter pipeline again — landing the page
# in the cross-process rate-limit queue.


default_symbol = st.session_state._recent_symbols[0] if st.session_state._recent_symbols else "000001"

with st.form("stock_detail_form", clear_on_submit=False):
    col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
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
    submitted = col4.form_submit_button("查询", width="stretch")

if not symbol:
    st.info("请输入股票代码。")
    st.stop()

# Always cache the form selection so we don't lose the user's latest
# typed-but-not-submitted input on rerun. Cheap because session_state
# writes don't trigger fetches.
st.session_state["_pending_symbol"] = symbol
st.session_state["_pending_range"] = range_label
st.session_state["_pending_adj"] = adj

# Show a hint until the user actually clicks "查询"; the cache is keyed
# on (symbol, range, adj) so once they hit the button, subsequent edits
# to other widgets do NOT bust the cache unless the form is resubmitted.
if not submitted:
    last = st.session_state.get("_last_rendered_key")
    cur = (symbol, range_label, adj)
    if last != cur:
        st.caption("点击「查询」加载 K 线；相同的 (代码, 范围, 复权) 组合 60 秒内会从缓存秒回。")
    st.stop()

# record to recent-list (dedupe, keep last 10, newest first)
if symbol and symbol not in st.session_state._recent_symbols:
    st.session_state._recent_symbols.insert(0, symbol)
    st.session_state._recent_symbols = st.session_state._recent_symbols[:10]

st.session_state["_last_rendered_key"] = (symbol, range_label, adj)


# ----- fetch bars ----------------------------------------------------------


end = date.today()
start = end - timedelta(days=_RANGE_OPTIONS[range_label])

try:
    cached = _fetch_bars_cached(symbol, start.isoformat(), end.isoformat(), adj)
except EmptyBarsError:
    st.error(f"AKShare 返回空数据 ({symbol} {start}–{end}, {_ADJ_LABELS[adj]})")
    st.stop()
except UnknownSymbolError:
    st.error(f"未知股票代码: {symbol!r}")
    st.stop()
except DataAdapterUnavailable as exc:
    st.error(f"网络拉取失败且无缓存:{exc}")
    st.stop()
except RateLimitExceeded as exc:
    st.error(f"⚠️ {exc}（其他进程已用满 20 req/min 上限）")
    st.stop()
except Exception as exc:
    # Safety net — the cache wrapper surfaces only the three documented
    # exceptions + RateLimitExceeded, but a bug inside ``to_dict`` or
    # elsewhere would otherwise leave the page silent. Surface anything.
    st.error(f"拉取 K 线时未预期异常: {type(exc).__name__}: {exc}")
    st.stop()

# Reconstruct DataFrame from the serialized cache payload.
source: str = cached["source"]
stale_seconds: int = cached["stale_seconds"]
records = cached["records"]
df = pd.DataFrame.from_records(records)
if "date" in df.columns:
    df["date"] = pd.to_datetime(df["date"]).dt.date

if source == "cache":
    st.warning(f"⚠️ 数据延迟: 来自本地缓存(共 {len(df)} 根 K 线, 已 {stale_seconds}s)")
elif source == "tencent":
    st.info("数据来源：腾讯财经（东方财富主路失败，已自动兜底）")
else:
    st.info(f"数据来源：东方财富实时（{len(df)} 根 K 线）")

if df.empty:
    st.info("无 K 线数据。")
    st.stop()

dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]]
ohlc = df[["open", "close", "low", "high"]].values.tolist()


# ----- historical signals (MarkPoint overlay) ------------------------------


# Per ui-pages.md: "Read past strategy_suggestions for this symbol under the
# active strategy — render them as MarkPoints color-coded by action."
active_id = get_active_strategy_id()
buy_pts: list[MarkPoint] = []
sell_pts: list[MarkPoint] = []
hold_pts: list[MarkPoint] = []
if active_id is not None:
    try:
        conn = get_connection()
        history = list_suggestions(conn, active_id, include_applied=True)
    except Exception:
        history = []
    # Map date -> MarkPoint coordinate. We index by the bar date so the
    # markers land exactly on the K-line bar (not on the suggestion's
    # generated_at, which has resolution finer than the daily bar).
    for s in history:
        if s.symbol != symbol:
            continue
        # Use target_price as the marker's y-value when present (price
        # precision beats the bar's high/low for visibility).
        y = s.target_price if s.target_price is not None else None
        gen = s.generated_at.date() if hasattr(s.generated_at, "date") else s.generated_at
        # nearest date in our series
        idx = _nearest_date_index(dates, gen)
        if idx is None:
            continue
        if y is None:
            # fall back to bar close at that idx
            y = float(ohlc[idx][1])
        coord: list = [dates[idx], float(y)]
        marker: MarkPoint = {"coord": coord, "value": s.action}
        bucket = {"buy": buy_pts, "sell": sell_pts, "hold": hold_pts}.get(s.action)
        if bucket is not None:
            bucket.append(marker)


# ----- k-line + volume (Grid) ----------------------------------------------


grid = build_kline_volume_grid(
    df,
    symbol=symbol,
    adj_label=_ADJ_LABELS[adj],
    buy_pts=buy_pts,
    sell_pts=sell_pts,
    hold_pts=hold_pts,
)
st_pyecharts(grid, height="600px")


# ----- "Add to universe" placeholder ---------------------------------------


st.button("添加到自选池", disabled=True, help="SPEC 开放问题 #1:等待产品决策。")
