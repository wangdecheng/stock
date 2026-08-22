"""pages/3_持仓管理.py — T5/T11 Page 4: positions.

Per ``ui-pages.md``:
  - Tabs: 真实持仓 | 虚拟账本
  - Real-positions tab:
      * ``st.data_editor`` over ``real_trades`` (insert / edit / delete rows)
      * Aggregated holdings panel (sum by symbol, avg cost)
  - Virtual-book tab:
      * Strategy dropdown (lists ``virtual_books`` keys)
      * Initial cash editor (writes ``virtual_books.initial_cash``)
      * Positions table (read-only)
      * Cash & top-up / withdraw sub-form (T11 addition)
      * Suggestions audit (``strategy_suggestions`` for this strategy)

Layout note: ``st.tabs`` evaluates both tab bodies during a script run;
we replaced the mid-tab ``st.stop()`` with an ``if not book_ids:`` guard
that renders the empty-state inline, so the page no longer truncates
when the user later visits the page after books appear.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from framework.persistence import (
    adjust_virtual_cash,
    delete_real_trade,
    ensure_virtual_book,
    get_virtual_book,
    InsufficientCashError,
    list_real_positions,
    list_real_trades,
    list_suggestions,
    list_virtual_positions,
    record_real_trade,
    set_virtual_initial_cash,
    update_real_trade,
    VirtualBookNotFoundError,
)
from framework.ui_runtime import get_connection


# ---------------------------------------------------------------------------
# helpers (hoisted so pyflakes sees them before use).
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> date:
    """Parse ``YYYY-MM-DD`` strictly. Streamlit's date_input would be nicer
    but column_config treats dates as text within an editable cell."""
    s = s.strip()
    if not s:
        raise ValueError("成交日期不能为空")
    return date.fromisoformat(s)


st.set_page_config(
    page_title="持仓管理",
    page_icon="💼",
    layout="wide",
)

st.title("💼 持仓管理")
st.caption("真实成交录入 + 虚拟账本快照(由 T4 持久化)")


tab_real, tab_virtual = st.tabs(["真实持仓", "虚拟账本"])


# ===========================================================================
# Tab 1: Real positions
# ===========================================================================


with tab_real:
    st.subheader("成交记录")
    conn = get_connection()
    trades = list_real_trades(conn)
    rows = [
        {
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "qty": t.qty,
            "price": t.price,
            "fee": t.fee,
            "executed_at": t.executed_at.isoformat(),
            "note": t.note or "",
        }
        for t in trades
    ]
    df = pd.DataFrame(rows)

    if df.empty:
        df = pd.DataFrame(columns=[
            "id", "symbol", "side", "qty", "price", "fee", "executed_at", "note",
        ])

    edited = st.data_editor(
        df,
        key="real_trades_editor",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "symbol": st.column_config.TextColumn("代码", max_chars=6, required=True),
            "side": st.column_config.SelectboxColumn(
                "方向", options=["buy", "sell"], required=True,
            ),
            "qty": st.column_config.NumberColumn("数量", min_value=1, step=1, required=True),
            "price": st.column_config.NumberColumn("价格", min_value=0.0, step=0.01, required=True),
            "fee": st.column_config.NumberColumn("手续费", min_value=0.0, step=0.01),
            "executed_at": st.column_config.TextColumn("成交日期(YYYY-MM-DD)", required=True),
            "note": st.column_config.TextColumn("备注"),
        },
    )

    if st.button("保存到数据库", type="primary", key="save_real"):
        try:
            edited_ids = set()
            for _, r in edited.iterrows():
                rid = int(r["id"]) if pd.notna(r["id"]) else None
                sym = str(r["symbol"]).strip()
                if not sym:
                    continue  # empty row, skip
                side = str(r["side"]).strip()
                qty = int(r["qty"])
                price = float(r["price"])
                fee = float(r["fee"]) if pd.notna(r["fee"]) else 0.0
                # executed_at: accept YYYY-MM-DD
                ed = _parse_date(str(r["executed_at"]))
                note = (str(r["note"]).strip() or None) if pd.notna(r["note"]) else None

                if rid is None:
                    new_id = record_real_trade(
                        conn,
                        symbol=sym,
                        side=side,
                        qty=qty,
                        price=price,
                        fee=fee,
                        executed_at=ed,
                        note=note,
                    )
                    edited_ids.add(new_id)
                else:
                    update_real_trade(
                        conn,
                        rid,
                        symbol=sym,
                        side=side,
                        qty=qty,
                        price=price,
                        fee=fee,
                        executed_at=ed,
                        note=note,
                    )
                    edited_ids.add(rid)
            # Deletes: any original id missing from edited is removed.
            original_ids = {t.id for t in trades}
            for rid in original_ids - edited_ids:
                delete_real_trade(conn, rid)
            conn.commit()
            st.success("已保存。")
            st.rerun()
        except Exception as exc:
            conn.rollback()
            st.error(f"保存失败:{type(exc).__name__}: {exc}")

    # ---- Aggregated holdings ----
    st.subheader("聚合持仓")
    agg = list_real_positions(conn)
    if not agg:
        st.caption("暂无真实持仓。")
    else:
        df_agg = pd.DataFrame(agg).rename(columns={
            "symbol": "代码",
            "qty": "持仓数量",
            "avg_cost": "平均成本",
            "buy_qty": "累计买入",
            "sell_qty": "累计卖出",
        })
        df_agg["持仓数量"] = df_agg["持仓数量"].astype(int)
        st.dataframe(df_agg, use_container_width=True, hide_index=True)


# ===========================================================================
# Tab 2: Virtual book
# ===========================================================================


with tab_virtual:
    st.subheader("虚拟账本")
    conn = get_connection()
    book_ids = [
        row[0] for row in conn.execute(
            "SELECT strategy_id FROM virtual_books ORDER BY strategy_id"
        ).fetchall()
    ]
    if not book_ids:
        st.caption(
            "尚无任何虚拟账本。等待 15:30 调度器产出的第一批建议,"
            "或到「仪表盘」点击 **应用到虚拟账本** 创建。"
        )
    else:
        sel = st.selectbox("选择策略", options=book_ids, index=0, key="_vb_strategy")
        book = get_virtual_book(conn, sel)
        if book is None:
            st.caption("选中的策略没有虚拟账本(可能已被删除)。")
        else:
            m1, m2 = st.columns(2)
            m1.metric("现金余额", f"¥{book.cash:,.2f}")
            m2.metric(
                "初始资金",
                f"¥{book.initial_cash:,.2f}",
                delta=f"{(book.cash - book.initial_cash):+,.2f}",
            )

            # ---- Cash top-up / withdraw (T11) ----------------------------
            st.markdown("**现金充值 / 提取**")
            with st.form(key=f"_cash_form_{sel}", clear_on_submit=True):
                tcol1, tcol2, tcol3 = st.columns([2, 2, 1])
                direction = tcol1.selectbox(
                    "方向",
                    options=["充值 (+)", "提取 (-)"],
                    key=f"_cash_dir_{sel}",
                )
                amount = tcol2.number_input(
                    "金额 (¥)",
                    min_value=0.0,
                    max_value=10_000_000.0,
                    step=1_000.0,
                    key=f"_cash_amt_{sel}",
                    help="充值与提取都会同时调整初始资金,使 P&L 不被外部现金流扭曲。",
                )
                submitted = tcol3.form_submit_button(
                    "提交",
                    type="primary",
                    use_container_width=True,
                )
            if submitted:
                if amount <= 0:
                    st.error("金额必须大于 0。")
                else:
                    delta = amount if direction == "充值 (+)" else -amount
                    try:
                        adjust_virtual_cash(conn, sel, delta)
                        st.success(
                            f"已{'充值' if delta > 0 else '提取'} ¥{amount:,.2f}。"
                        )
                        st.rerun()
                    except InsufficientCashError as exc:
                        st.error(f"提取失败:{exc}")
                    except VirtualBookNotFoundError:
                        st.error("虚拟账本不存在,请重新选择策略。")

            # ---- Initial-cash editor (T11) ------------------------------
            st.markdown("**初始资金调整(仅改 P&L 基准,不动现金)**")
            ic1, ic2 = st.columns([3, 1])
            new_initial = ic1.number_input(
                "新初始资金 (¥)",
                min_value=0.0,
                max_value=100_000_000.0,
                value=float(book.initial_cash),
                step=10_000.0,
                key=f"_initial_cash_{sel}",
                help="改这个值只会重设 P&L 基准;现金余额不变。",
            )
            if ic2.button("保存", key=f"_initial_save_{sel}"):
                try:
                    set_virtual_initial_cash(conn, sel, float(new_initial))
                    st.success(f"已更新初始资金 → ¥{new_initial:,.2f}")
                    st.rerun()
                except ValueError as exc:
                    st.error(f"保存失败:{exc}")

            st.markdown("**持仓**")
            positions = list_virtual_positions(conn, sel)
            if not positions:
                st.caption("当前无持仓。")
            else:
                df_pos = pd.DataFrame([
                    {
                        "代码": p.symbol,
                        "数量": p.qty,
                        "平均成本": p.avg_cost,
                        "建仓日期": p.opened_at.isoformat(),
                    }
                    for p in positions
                ])
                st.dataframe(df_pos, use_container_width=True, hide_index=True)

            st.markdown("**建议审计**")
            audit = list_suggestions(conn, sel, include_applied=True)
            if not audit:
                st.caption("暂无建议记录。")
            else:
                df_audit = pd.DataFrame([
                    {
                        "#": s.id,
                        "代码": s.symbol,
                        "动作": s.action,
                        "目标数量": s.target_qty,
                        "目标价": s.target_price,
                        "生成时间": s.generated_at.isoformat(timespec="seconds"),
                        "已应用": "✅" if s.applied else "⏳",
                        "应用时间": (
                            s.applied_at.isoformat(timespec="seconds")
                            if s.applied_at else ""
                        ),
                    }
                    for s in audit
                ])
                st.dataframe(df_audit, use_container_width=True, hide_index=True)

    # ---- Operator escape hatch: bootstrap a book manually -----------------
    # If no virtual book exists yet for any strategy, allow the operator to
    # create one with a chosen initial cash. Mirrors the spec's "Cash &
    # top-up / withdraw" intent for the bootstrap case.
    if not book_ids:
        st.divider()
        st.markdown("**手动创建虚拟账本**")
        with st.form(key="_bootstrap_form"):
            bcol1, bcol2 = st.columns([3, 1])
            new_strategy = bcol1.text_input(
                "策略 id",
                value="etf_rebalance",
                key="_boot_strategy",
                help="必须与「策略管理」页里的某个已发现策略一致。",
            )
            new_cash = bcol2.number_input(
                "初始资金 (¥)",
                min_value=0.0,
                max_value=100_000_000.0,
                value=100_000.0,
                step=10_000.0,
                key="_boot_cash",
            )
            if st.form_submit_button("创建", type="primary"):
                try:
                    ensure_virtual_book(conn, new_strategy, initial_cash=float(new_cash))
                    st.success(f"已为 {new_strategy} 创建虚拟账本。")
                    st.rerun()
                except Exception as exc:
                    st.error(f"创建失败:{type(exc).__name__}: {exc}")
