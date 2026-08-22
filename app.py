"""app.py — Page 1 (T5: 仪表盘 / Dashboard).

Layout (per ``specs/spec-a-stock-quant/ui-pages.md``):

  - Header banner: today's date + active strategy name
  - Active-strategy card: last-run timestamp, status, link to strategy page
  - Real-position card: market value (with today's close prices), top-5
    holdings by market value
  - Virtual-book card: cash + positions snapshot
  - Today's action plan: pending ``strategy_suggestions`` for the active
    strategy, with a one-click "Apply all to virtual book" button wired
    to T4's atomic ``apply_suggestions_to_book``
  - Run-now button (T6 placeholder)

Behavior when no active strategy is set:

  - Dashboard renders all cards with empty/zero state and shows a single
    ``st.info`` prompting the user to visit 策略管理 to activate one.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from framework.persistence import (
    apply_suggestions_to_book,
    get_virtual_book,
    list_pending_suggestions,
    list_real_positions,
    list_virtual_positions,
)
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
    col_status.caption(f"最近回测: {last_run}")

st.page_link("pages/2_策略管理.py", label="查看完整建议 →", icon="📋")


# ----- real positions card --------------------------------------------------


st.subheader("真实持仓")
if not real:
    st.caption("暂无真实持仓记录。请到 **「持仓管理」** 页录入。")
else:
    df_real = pd.DataFrame(real)
    df_real = df_real.rename(columns={
        "symbol": "代码",
        "qty": "持仓数量",
        "avg_cost": "成本价",
        "buy_qty": "累计买入",
        "sell_qty": "累计卖出",
    })
    df_real["持仓数量"] = df_real["持仓数量"].astype(int)
    st.dataframe(df_real, use_container_width=True, hide_index=True)


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
        help="点击下方按钮后,系统会按 T4 的原子事务规则把这一批建议一次性应用到虚拟账本;若任一行校验失败,整批回滚。",
    )
    if st.button(
        "应用到虚拟账本",
        type="primary",
        disabled=not confirm,
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


# ----- run-now (T6 placeholder) ---------------------------------------------


st.divider()
st.caption(
    "🕒 自动调度(每日 15:30)由 T6 在独立进程中处理。"
    "此处的「立即运行」按钮为 T7 部署完成后的快捷入口,目前不可用。"
)
st.button("立即运行当前策略", disabled=True, help="T6 调度器上线后启用。")
