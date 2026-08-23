"""pages/2_策略管理.py — T5 Page 3: strategy admin.

Per ``ui-pages.md``:
  - Strategy list (discovered by ``discover_strategies()``); per row: name,
    file path, persisted params file (or "尚未编辑"), state file size
  - Row actions: 激活 / 重置 state / 删除 params.json / 查看文件
  - Selected strategy panel:
      * param form auto-rendered from ``Strategy.__init__`` annotations
      * Save → atomic ``save_params`` (writes the params envelope)
      * Run now → backtest with default 1Y window (writes ``backtests`` row)
      * Backtest history table (from ``backtests``)
  - Activation rule visualization: 需 30 天内有一次回测 (via ``has_recent_run``)
"""

from __future__ import annotations

import inspect
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from framework.backtest import has_recent_run
from framework.strategy import discover_strategies, load_params, save_params
from framework.strategy.persistence import (
    save_state,
    state_path,
)
from framework.ui_runtime import (
    get_active_strategy_id,
    get_connection,
    set_active_strategy_id,
    strategies_dir,
)


st.set_page_config(
    page_title="策略管理",
    page_icon="⚙️",
    layout="wide",
)

st.title("⚙️ 策略管理")
st.caption("discover + 参数编辑 + 激活 + 最近回测记录")


# ---------------------------------------------------------------------------
# Param-widget helpers (hoisted so pyflakes sees them before use).
# ---------------------------------------------------------------------------


def _format_annotation(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "未标注"
    return getattr(annotation, "__name__", str(annotation))


def _render_param_widget(
    label: str,
    pname: str,
    default: Any,
    annotation: Any,
    *,
    key_prefix: str,
) -> Any:
    """Render one widget per ``__init__`` parameter. Returns the new value
    or ``None`` if the parameter type is unsupported.

    Annotation dispatch (per strategy-interface.md §"Param form UX"):
      * ``int``   → ``number_input`` (integer step)
      * ``float`` → ``number_input``
      * ``str``   → ``text_input``
      * ``bool``  → ``checkbox``
      * other     → render a text input and let the user paste JSON
    """
    key = f"{key_prefix}{pname}"
    if annotation is int or annotation == "int":
        return st.number_input(label, value=int(default), step=1, key=key, format="%d")
    if annotation is float or annotation == "float":
        return float(st.number_input(label, value=float(default), key=key))
    if annotation is str or annotation == "str":
        return st.text_input(label, value=str(default), key=key)
    if annotation is bool or annotation == "bool":
        return bool(st.checkbox(label, value=bool(default), key=key))
    # Unsupported type — fall back to JSON text so the user can still set it.
    if default is None:
        initial_json = "null"
    else:
        try:
            initial_json = json.dumps(default, ensure_ascii=False)
        except TypeError:
            # Non-serializable default (e.g. a class, a callable). Don't
            # crash the page — start with an empty JSON buffer and warn.
            initial_json = ""
            st.warning(
                f"{pname} 的默认值不可 JSON 序列化,已留空。请手动填入合法 JSON。"
            )
    text = st.text_input(f"{label} (JSON)", value=initial_json, key=key)
    try:
        return json.loads(text)
    except Exception:
        st.warning(f"{pname} 不是合法 JSON;未保存。")
        return None


# ---------------------------------------------------------------------------
# Discover (cache the registry per session so widget jitter doesn't trigger
# a full re-import of all strategies on every rerun).
# ---------------------------------------------------------------------------


@st.cache_data(ttl=60, show_spinner=False)
def _discover_cached(_dir_str: str) -> dict[str, type]:
    return discover_strategies(force_reload=True)


registry = _discover_cached(str(strategies_dir()))

if not registry:
    st.warning(f"未在 `{strategies_dir()}` 发现策略。请确认目录与 .py 文件存在。")
    st.stop()


# Current active strategy (mirror of config table) so we can highlight it
# in the list and disable re-activation buttons.
active_id = get_active_strategy_id()


# ---------------------------------------------------------------------------
# Strategy list
# ---------------------------------------------------------------------------


st.subheader(f"已发现 {len(registry)} 个策略")

# Build a DataFrame view so the list is scannable; per-row action buttons
# live below in an expander so they don't fight with column width.
rows = []
for name, cls in registry.items():
    file_path = inspect.getfile(cls)
    params_file = strategies_dir() / f"{name}.params.json"
    state_file = state_path(strategies_dir(), name)
    rows.append({
        "name": name,
        "file": file_path,
        "params_file": "已保存" if params_file.is_file() else "未编辑",
        "state_size": state_file.stat().st_size if state_file.is_file() else 0,
        "active": name == active_id,
    })
df_list = pd.DataFrame(rows)
st.dataframe(df_list, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Per-strategy actions
# ---------------------------------------------------------------------------


st.subheader("策略操作")

for name, cls in registry.items():
    with st.expander(f"📦 {name}{'  ✅ 当前激活' if name == active_id else ''}"):
        cls_module = cls.__module__
        cls_qualname = f"{cls_module}.{cls.__name__}"
        st.caption(f"`{cls_qualname}` · 文件 `{inspect.getfile(cls)}`")

        bcols = st.columns(4)

        # ---- 激活 ----
        with bcols[0]:
            is_active = name == active_id
            recent = has_recent_run(get_connection(), name, days=30)
            if is_active:
                st.button("✅ 已激活", key=f"act_{name}", disabled=True)
            elif not recent:
                st.button(
                    "需要先回测",
                    key=f"act_{name}",
                    disabled=True,
                    help="激活条件:30 天内至少有一次回测。",
                )
            else:
                if st.button("激活", key=f"act_{name}", type="primary"):
                    set_active_strategy_id(name)
                    st.success(f"已激活 {name}。刷新仪表盘查看。")
                    st.rerun()

        # ---- 重置 state ----
        with bcols[1]:
            if st.button("重置 state", key=f"rst_{name}"):
                save_state(strategies_dir(), name, {})
                st.info(f"{name} 的 state.json 已重置为空 dict。")
                st.rerun()

        # ---- 删除 params.json ----
        with bcols[2]:
            pf = strategies_dir() / f"{name}.params.json"
            if pf.is_file():
                if st.button("删除 params.json", key=f"del_{name}"):
                    pf.unlink()
                    st.info(f"已删除 {pf.name}。")
                    st.rerun()
            else:
                st.button("删除 params.json", key=f"del_{name}", disabled=True)

        # ---- 查看文件 ----
        with bcols[3]:
            st.code(
                Path(inspect.getfile(cls)).read_text(encoding="utf-8"),
                language="python",
            )

        # ---- 参数表单 ----
        st.markdown("**参数编辑**")
        existing = load_params(strategies_dir(), name)
        existing_params: dict = {}
        if existing and isinstance(existing.get("params"), dict):
            existing_params = existing["params"]

        sig = inspect.signature(cls.__init__)
        new_params: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            # ``**kwargs`` / ``*args`` are structural catch-alls, not user-
            # facing parameters; rendering a JSON widget for them would
            # try to ``json.dumps(inspect._empty)`` and blow up.
            if param.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                continue
            default = existing_params.get(pname, param.default)
            annotation = param.annotation
            label = f"{pname} ({_format_annotation(annotation)})"
            value = _render_param_widget(label, pname, default, annotation, key_prefix=f"p_{name}_")
            if value is not None:
                new_params[pname] = value

        if st.button("保存参数", key=f"save_{name}"):
            envelope = {
                "strategy_class": cls_qualname,
                "strategy_name": name,
                "params": new_params,
                "updated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            }
            save_params(strategies_dir(), name, envelope)
            st.success(f"已保存到 {name}.params.json")
            st.rerun()

        # ---- Run now (quick backtest) ----
        st.markdown("**快速回测**")
        rcols = st.columns([1, 1, 1, 1])
        end_date = date.today()
        start_date = end_date.replace(year=end_date.year - 1)
        b_start = rcols[0].date_input("开始", value=start_date, key=f"bs_{name}")
        b_end = rcols[1].date_input("结束", value=end_date, key=f"be_{name}")
        b_cash = rcols[2].number_input(
            "初始现金", value=100_000.0, step=10_000.0, key=f"bc_{name}"
        )
        if rcols[3].button("运行", key=f"run_{name}"):
            # The actual Engine.run() requires DataAdapter + universe + calendar.
            # We delegate to ``pages/4_回测.py`` for the heavy lifting rather
            # than duplicating the wiring here; the dashboard link is the
            # canonical entry point. This button is a UX shortcut that simply
            # persists the user's intent and routes them to the backtest page
            # with prefilled values.
            st.session_state["_redirect_backtest"] = {
                "strategy_id": name,
                "start": b_start.isoformat(),
                "end": b_end.isoformat(),
                "initial_cash": float(b_cash),
            }
            st.info("已设置回测参数,请到「回测」页点击 Run。")
            st.page_link("pages/4_回测.py", label="打开回测页 →", icon="📈")

        # ---- 历史 ----
        st.markdown("**历史回测**")
        conn = get_connection()
        hist_rows = conn.execute(
            "SELECT id, start, end, initial_cash, ran_at, metrics_json, equity_path "
            "FROM backtests WHERE strategy_id = ? ORDER BY ran_at DESC LIMIT 20",
            (name,),
        ).fetchall()
        if not hist_rows:
            st.caption("暂无回测记录。")
        else:
            df_hist = pd.DataFrame(hist_rows, columns=[
                "id", "start", "end", "initial_cash", "ran_at", "metrics_json", "equity_path",
            ])
            # Surface a few headline metrics inline so the user can scan
            # without opening the parquet.
            def _headline(metrics_json: str) -> str:
                try:
                    m = json.loads(metrics_json)
                except Exception:
                    return "—"
                parts = [
                    f"总收益={m.get('total_return', 0) * 100:.1f}%",
                    f"夏普={m.get('sharpe', 0):.2f}",
                    f"最大回撤={m.get('max_drawdown', 0) * 100:.1f}%",
                ]
                return "  ·  ".join(parts)
            df_hist["指标"] = df_hist["metrics_json"].map(_headline)
            st.dataframe(
                df_hist[["id", "start", "end", "initial_cash", "ran_at", "指标"]],
                use_container_width=True,
                hide_index=True,
            )
