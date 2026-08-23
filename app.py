"""app.py — Page 1 (T5/T11: 仪表盘 / Dashboard).

Layout (per ``specs/spec-a-stock-quant/ui-pages.md`` + T11 Resolution):

  - Header banner: today's date + active strategy name
  - Active-strategy card: last-run timestamp, status (success / stale),
    link to strategy page
  - Real-position card: market value, today's P&L, top-5 holdings
  - Virtual-book card: cash + positions snapshot
  - Virtual-book equity curve (Pyecharts line, portfolio vs benchmark)
    read from the most-recent backtest parquet for the active strategy,
    with a 真实 / 虚拟 / 全部 toggle that controls which series display.
  - Today's action plan: pending ``strategy_suggestions`` for the active
    strategy, with a one-click "Apply all to virtual book" button wired
    to T4's atomic ``apply_suggestions_to_book``
  - Run-now button: launches ``framework.runner.scheduled_run`` as a
    *subprocess* (scheduler.md §"Hard rule" — runner must not be imported
    inside the Streamlit process), guarded by a confirmation checkbox.

Behavior when no active strategy is set:

  - Dashboard renders all cards with empty/zero state and shows a single
    ``st.info`` prompting the user to visit 策略管理 to activate one.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from typing import Optional

import pandas as pd
import streamlit as st
from streamlit_echarts import st_pyecharts

from framework.charts.equity import build_equity_curve
from framework.persistence import (
    apply_suggestions_to_book,
    get_virtual_book,
    list_pending_suggestions,
    list_real_positions,
    list_virtual_positions,
)
from framework.persistence.positions import latest_real_trades
from framework.ui_runtime import (
    get_active_strategy_id,
    get_connection,
)


st.set_page_config(
    page_title="A 股量化 · 仪表盘",
    page_icon="📈",
    layout="wide",
)

st.title("📈 A 股量化 · 仪表盘")
st.caption(f"今天 {date.today().isoformat()} · 持仓概览 + 今日操作计划")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionMarketValue:
    """One real-position row with today's close + P&L. The dashboard
    renders one row of these per held symbol."""

    symbol: str
    qty: int
    avg_cost: Optional[float]
    last_price: float
    last_price_source: str   # "akshare" / "latest_trade" / "avg_cost" fallback
    market_value: float
    unrealized_pnl: float


# ---------------------------------------------------------------------------
# Cache: per-render DB reads. ``st.cache_data`` keyed on the active id so
# the dashboard doesn't re-query on every widget jitter, but a different
# active strategy immediately refreshes the panels.
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30, show_spinner=False)
def _real_positions_cached(_active: str | None) -> list[dict]:
    return list_real_positions(get_connection())


@st.cache_data(ttl=30, show_spinner=False)
def _virtual_book_cached(_active: str | None) -> tuple | None:
    """``(book_dict, positions_list)`` so the linter is happy about the
    single-call cache; ``None`` when no book exists yet."""
    if _active is None:
        return None
    conn = get_connection()
    book = get_virtual_book(conn, _active)
    if book is None:
        return None
    positions = [
        {
            "symbol": p.symbol,
            "qty": p.qty,
            "avg_cost": p.avg_cost,
            "opened_at": p.opened_at.isoformat(),
        }
        for p in list_virtual_positions(conn, _active)
    ]
    return (book.__dict__, positions)


@st.cache_data(ttl=15, show_spinner=False)
def _pending_suggestions_cached(_active: str | None) -> list[dict]:
    if _active is None:
        return []
    conn = get_connection()
    out = []
    for s in list_pending_suggestions(conn, _active):
        out.append({
            "id": s.id,
            "symbol": s.symbol,
            "action": s.action,
            "target_qty": s.target_qty,
            "target_price": s.target_price,
            "confidence": s.confidence,
            "reason": s.reason,
            "generated_at": s.generated_at.isoformat(timespec="seconds"),
        })
    return out


@st.cache_data(ttl=15, show_spinner=False)
def _latest_equity_cached(_active: str | None) -> dict | None:
    """Read the most recent backtest parquet for the active strategy.

    Returns ``{"dates": [...], "portfolio": [...], "benchmark": [...],
    "run_id": int, "ran_at": str}`` or ``None`` when no backtest exists.
    Cached so widget jitter doesn't re-read the parquet on every render.
    """
    if _active is None:
        return None
    row = get_connection().execute(
        "SELECT id, ran_at, equity_path FROM backtests "
        "WHERE strategy_id = ? ORDER BY ran_at DESC LIMIT 1",
        (_active,),
    ).fetchone()
    if row is None:
        return None
    run_id, ran_at, equity_path = row
    p = Path(equity_path)
    if not p.is_file():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty:
        return None
    return {
        "dates": df["date"].astype(str).tolist(),
        "portfolio": df["portfolio_value"].astype(float).tolist(),
        "benchmark": df["benchmark_value"].astype(float).tolist(),
        "run_id": int(run_id),
        "ran_at": str(ran_at),
    }


# ---------------------------------------------------------------------------
# Market-value helper (per-symbol)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=60, show_spinner=False)
def fetch_realtime_prices_cached(symbols: tuple[str, ...]) -> dict[str, float]:
    """Best-effort latest mark-to-market prices for a symbol set.

    Reads go through ``AKShareAdapter.get_bars`` (which has its own
    @ratelimit-decorated window of 20 req/min). Caching the network calls
    here — keyed on the *sorted symbol tuple* — keeps the dashboard from
    spending the whole rate budget on rerenders: 10 holdings × N widget
    jitters / page reload was observed as a steady 60 s spinner before
    ``st.cache_data`` was introduced.

    Network errors are swallowed per-symbol so a single AKShare outage
    doesn't blank the whole table; the caller can fall back to the last
    real-trade price or to avg_cost.
    """
    if not symbols:
        return {}
    from framework.data.adapter import AKShareAdapter  # lazy: keep app import-graph minimal
    adapter = AKShareAdapter(cache_dir=Path("data/cache"))
    end = date.today()
    start = end - timedelta(days=10)
    out: dict[str, float] = {}
    for sym in symbols:
        try:
            result = adapter.get_bars(sym, start, end, adj="qfq", frequency="daily")
        except Exception:
            continue
        if result.df.empty:
            continue
        try:
            out[sym] = float(result.df["close"].iloc[-1])
        except Exception:
            continue
    return out


def _fetch_latest_prices(
    symbols: list[str],
    *,
    fallback_prices: dict[str, float],
) -> dict[str, tuple[float, str]]:
    """Mark-to-market price lookup for the dashboard.

    Order of preference (returned with a ``source_label``):
      1. AKShare's most recent close from a 5-day window.
         — read via :func:`fetch_realtime_prices_cached` to skip the
           upstream rate-limit wall on subsequent renders.
      2. ``fallback_prices[symbol]`` (the latest real-trade price).
      3. ``None`` — caller can fall back to avg_cost.

    Network errors are handled inside the cached helper; this function
    just stitches the realtime and fallback views together. Returns
    ``{symbol: (price, source_label)}``.
    """
    if not symbols:
        return {}
    realtime = fetch_realtime_prices_cached(tuple(sorted(symbols)))
    out: dict[str, tuple[float, str]] = {}
    for sym in symbols:
        if sym in realtime:
            out[sym] = (float(realtime[sym]), "akshare")
            continue
        fb = fallback_prices.get(sym)
        if fb is not None:
            out[sym] = (float(fb), "latest_trade")
    return out


def _position_market_values(real: list[dict]) -> list[PositionMarketValue]:
    """Compute market value + unrealized P&L for each real position.

    Order of price preference: AKShare last close → latest trade price →
    avg_cost (so the dashboard never shows None / NaN in the table even
    if both AKShare and the trade log fail to resolve a price).
    """
    if not real:
        return []
    symbols = [r["symbol"] for r in real]
    fb = latest_real_trades(get_connection(), symbols)
    fb_prices = {sym: row["price"] for sym, row in fb.items()}
    latest = _fetch_latest_prices(symbols, fallback_prices=fb_prices)
    out: list[PositionMarketValue] = []
    for r in real:
        sym = r["symbol"]
        qty = int(r["qty"])
        avg_cost = r.get("avg_cost")
        if sym in latest:
            price, source = latest[sym]
        elif avg_cost is not None:
            price, source = float(avg_cost), "avg_cost"
        elif fb_prices.get(sym) is not None:
            price, source = float(fb_prices[sym]), "latest_trade"
        else:
            # We genuinely have no price for this row; skip rather than
            # render a NaN column that would break the dataframe sort.
            continue
        mv = float(qty) * price
        if avg_cost is not None:
            pnl = (float(price) - float(avg_cost)) * float(qty)
        else:
            pnl = 0.0
        out.append(PositionMarketValue(
            symbol=sym,
            qty=qty,
            avg_cost=avg_cost,
            last_price=price,
            last_price_source=source,
            market_value=mv,
            unrealized_pnl=pnl,
        ))
    return out


# ---------------------------------------------------------------------------
# Load + render
# ---------------------------------------------------------------------------


active_id = get_active_strategy_id()
if active_id is None:
    st.info(
        "👋 还没有激活的策略。请先到 **「策略管理」** 页选一个策略并完成一次回测。"
    )
    st.stop()

real = _real_positions_cached(active_id)
book_tuple = _virtual_book_cached(active_id)
pending = _pending_suggestions_cached(active_id)
equity = _latest_equity_cached(active_id)


# ----- header / active strategy card ----------------------------------------


col_hdr, col_status = st.columns([3, 1])
col_hdr.subheader(f"当前策略 · `{active_id}`")
last_run_row = get_connection().execute(
    "SELECT ran_at FROM backtests WHERE strategy_id = ? ORDER BY ran_at DESC LIMIT 1",
    (active_id,),
).fetchone()
if last_run_row is None:
    col_status.warning("⚠️ 无回测记录")
else:
    last_run = last_run_row[0]
    # "Stale" badge: > 24h since the last backtest (per ui-pages.md).
    try:
        last_dt = datetime.fromisoformat(str(last_run).replace(" ", "T"))
        stale = datetime.now() - last_dt > timedelta(hours=24)
    except Exception:
        stale = False
    col_status.caption(
        f"最近回测: {last_run}{'  ⚠️ 数据陈旧' if stale else ''}"
    )

# page_link is wrapped in try/except because AppTest (the testing harness)
# doesn't fully support it — it's a navigation helper that requires a
# live page context. The dashboard renders fine without the link in tests.
try:
    st.page_link("pages/2_策略管理.py", label="查看完整建议 →", icon="📋")
except Exception:
    pass


# ----- real positions card --------------------------------------------------


st.subheader("真实持仓")
mv_rows = _position_market_values(real) if real else []
if not real:
    st.caption("暂无真实持仓记录。请到 **「持仓管理」** 页录入。")
else:
    df_real = pd.DataFrame([
        {
            "代码": r["symbol"],
            "持仓数量": int(r["qty"]),
            "成本价": r["avg_cost"],
            "累计买入": int(r["buy_qty"]),
            "累计卖出": int(r["sell_qty"]),
        }
        for r in real
    ])
    # ---- market value + today's P&L + top-5 summary -------------------------
    if mv_rows:
        total_mv = sum(m.market_value for m in mv_rows)
        total_pnl = sum(m.unrealized_pnl for m in mv_rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("总市值", f"¥{total_mv:,.2f}")
        c2.metric(
            "浮动盈亏 (vs 成本)",
            f"¥{total_pnl:+,.2f}",
            delta=f"{total_pnl / total_mv * 100:+.2f}%" if total_mv else None,
            help=(
                "(当前价 − 平均成本) × 持仓数量。"
                "这是未实现盈亏,不是今天的 P&L —— "
                "今日 P&L 需要昨日收盘价,需另接 bar 数据。"
            ),
        )
        # Stale-data badge when any row fell back to avg_cost / latest_trade.
        stale_sources = {m.symbol for m in mv_rows if m.last_price_source != "akshare"}
        c3.caption(
            "⚠️ 部分价格来自兜底数据源"
            if stale_sources
            else "价格来源: AKShare 最新收盘"
        )
        # Top-5 by market value (the spec calls this out explicitly).
        mv_rows_sorted = sorted(mv_rows, key=lambda m: m.market_value, reverse=True)
        df_top = pd.DataFrame([
            {
                "代码": m.symbol,
                "数量": m.qty,
                "最新价": round(m.last_price, 2),
                "市值": round(m.market_value, 2),
                "浮动盈亏": round(m.unrealized_pnl, 2),
            }
            for m in mv_rows_sorted[:5]
        ])
        st.markdown("**Top-5 持仓 (按市值)**")
        st.dataframe(df_top, use_container_width=True, hide_index=True)

    st.dataframe(df_real, use_container_width=True, hide_index=True)


# ----- virtual book equity curve --------------------------------------------


st.subheader("虚拟账本曲线")
if equity is None:
    st.caption(
        "尚未找到该策略的回测净值曲线。先到 **「回测」** 页跑一次回测,"
        "或等待 15:30 调度器产出今日计划后再回来查看。"
    )
else:
    toggle = st.radio(
        "显示",
        options=["策略净值", "基准", "两者"],
        index=0,
        horizontal=True,
        key="_equity_toggle",
        help="曲线数据源为最近一次回测的 parquet。'策略净值'对应虚拟账本;实际虚拟账本按应用建议的时间点更新,不在此曲线中。",
    )
    if toggle == "策略净值":
        line = build_equity_curve(
            equity["dates"], equity["portfolio"], None,
            title=f"虚拟账本净值 (来自回测 #{equity['run_id']})",
            height="320px",
        )
    elif toggle == "基准":
        line = build_equity_curve(
            equity["dates"], equity["benchmark"], None,
            title=f"基准净值 (来自回测 #{equity['run_id']})",
            height="320px",
        )
    else:
        line = build_equity_curve(
            equity["dates"], equity["portfolio"], equity["benchmark"],
            title=f"虚拟账本净值 vs 基准 (来自回测 #{equity['run_id']})",
            height="320px",
        )
    st_pyecharts(line, height="320px")
    st.caption(
        f"数据源: 回测 #{equity['run_id']} @ {equity['ran_at']}。"
        "该曲线反映回测的策略净值,实际虚拟账本按应用建议的时间点更新,不在此曲线中。"
    )


# ----- virtual book snapshot ------------------------------------------------


st.subheader("虚拟账本")
if book_tuple is None:
    st.caption(
        "尚未创建虚拟账本 — 在「操作计划」点击 **应用到虚拟账本** 后会自动创建,初始现金 ¥100,000。"
    )
else:
    book_dict, positions = book_tuple
    cash = float(book_dict["cash"])
    initial_cash = float(book_dict["initial_cash"])
    mcol1, mcol2 = st.columns(2)
    mcol1.metric("现金余额", f"¥{cash:,.2f}")
    mcol2.metric("初始资金", f"¥{initial_cash:,.2f}", delta=f"{(cash - initial_cash):+,.2f}")
    if positions:
        df_pos = pd.DataFrame(positions).rename(columns={
            "symbol": "代码",
            "qty": "持仓数量",
            "avg_cost": "成本价",
            "opened_at": "建仓日期",
        })
        st.dataframe(df_pos, use_container_width=True, hide_index=True)
    else:
        st.caption("当前无持仓。")


# ----- today's action plan --------------------------------------------------


st.subheader("今日操作计划")
if not pending:
    st.caption("当前没有待应用的建议。等待 15:30 自动调度产出新的操作计划。")
else:
    df_plan = pd.DataFrame(pending).rename(columns={
        "id": "#",
        "symbol": "代码",
        "action": "动作",
        "target_qty": "目标数量",
        "target_price": "目标价",
        "confidence": "置信度",
        "reason": "理由",
        "generated_at": "生成时间",
    })
    st.dataframe(df_plan, use_container_width=True, hide_index=True)

    confirm = st.checkbox(
        "我已确认以上操作要应用到虚拟账本",
        value=False,
        key="_apply_confirm",
        help="点击下方按钮后,系统会按 T4 的原子事务规则把这一批建议一次性应用到虚拟账本;若任一行校验失败,整批回滚。",
    )
    if st.button(
        "应用到虚拟账本",
        type="primary",
        disabled=not confirm,
        key="_apply_btn",
        help="原子写入;失败则整批回滚。",
    ):
        try:
            conn = get_connection()
            summary = apply_suggestions_to_book(
                conn,
                strategy_id=active_id,
                suggestion_ids=[r["id"] for r in pending],
            )
            st.success(
                f"✅ 已应用 {len(summary)} 条建议。"
                f" 详细变更:{summary}"
            )
            # Bust the cache so the virtual-book card refreshes immediately.
            _virtual_book_cached.clear()
            _pending_suggestions_cached.clear()
        except Exception as exc:
            st.error(f"应用失败,整批回滚: {type(exc).__name__}: {exc}")


# ----- run-now (T11: wire to subprocess) ------------------------------------


st.divider()
st.subheader("立即运行当前策略")
st.caption(
    "调用 15:30 自动调度的同一脚本 (``python -m framework.runner.scheduled_run``)"
    "作为子进程跑一次 — 这保证长时间运行的策略不会冻结 UI 线程"
    "(per ``scheduler.md`` §\"Hard rule\")。"
)

# Suppress accidental double-clicks within a short window.
NOW_BTN_KEY = "_run_now_clicked_at"
confirm_run = st.checkbox(
    "我已确认要立即跑批当前策略(可能需要数十秒)",
    value=False,
    key="_run_confirm",
)
run_now_clicked = st.button(
    "立即运行当前策略",
    type="primary",
    disabled=not confirm_run,
    key="_run_now_btn",
)
if run_now_clicked:
    last_clicked = st.session_state.get(NOW_BTN_KEY)
    if last_clicked and (datetime.now() - last_clicked).total_seconds() < 5:
        st.warning("刚刚才点过一次,请稍候。")
    else:
        st.session_state[NOW_BTN_KEY] = datetime.now()
        runner_module = (
            Path(__file__).resolve().parent / "framework" / "runner" / "__main__.py"
        )
        cmd = [
            sys.executable,
            str(runner_module),
            "--db-path", "data/app.db",
            "--strategies-dir", "strategies",
            "--strategy", active_id,
        ]
        with st.spinner("策略运行中…(最长 120 秒)"):
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(Path(__file__).resolve().parent),
                )
            except subprocess.TimeoutExpired:
                st.error("⏱️ 子进程超过 120 秒未返回。检查服务器/网络。")
                completed = None  # type: ignore[assignment]
        if completed is not None:
            if completed.returncode == 0:
                st.success("✅ 运行完成。建议已写入 strategy_suggestions。")
                _pending_suggestions_cached.clear()
                _latest_equity_cached.clear()
            else:
                st.error(
                    f"❌ 运行失败(exit={completed.returncode})。"
                    f"\nstdout: {completed.stdout[-400:] if completed.stdout else '(empty)'}"
                    f"\nstderr: {completed.stderr[-400:] if completed.stderr else '(empty)'}"
                )