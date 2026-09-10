"""pages/4_回测.py — T5 Page 5: backtest.

Per ``ui-pages.md``:
  - Inputs: strategy (dropdown) · date range · initial cash · universe
  - Run button → progress bar → result panel
  - Result panel:
      * PnL curve (Pyecharts line, portfolio vs benchmark)
      * Six metric cards (``st.metric``, label + delta vs prior run)
      * Fills table (sortable)
      * "Save to history" — implicit; the run writes to ``backtests``

Behavior:
  - Reads the strategy class via ``discover_strategies()`` and instantiates
    it from ``strategies/<name>.params.json`` (or its ``__init__`` defaults
    if no params file exists).
  - Calls ``AKShareAdapter.get_calendar(start, end)`` for the trading-day
    window. Network failures show a graceful ``st.error`` instead of crashing.
  - Calls ``Engine.run()`` and ``save_run()`` from T3, then renders.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from pyecharts import options as opts
from pyecharts.charts import Line
from streamlit_echarts import st_pyecharts

from framework.backtest import Engine, EngineConfig, metrics_compute, save_run
from framework.data.adapter import (
    AKShareAdapter,
    DataAdapterUnavailable,
    EmptyBarsError,
    UnknownSymbolError,
)
from framework.strategy import discover_strategies, load_params
from framework.ui_runtime import get_connection, strategies_dir


st.set_page_config(
    page_title="回测",
    page_icon="📈",
    layout="wide",
)

st.markdown("#### 📈 回测")
st.caption("策略 + 时间窗口 + 初始现金 → PnL + 7 个指标")


# ---------------------------------------------------------------------------
# singletons + discovery
# ---------------------------------------------------------------------------


def get_adapter() -> AKShareAdapter:
    return AKShareAdapter(cache_dir=Path("data/cache"))


@st.cache_data(ttl=60, show_spinner=False)
def _discover_cached(_dir_str: str) -> dict[str, type]:
    return discover_strategies(force_reload=True)


registry = _discover_cached(str(strategies_dir()))
if not registry:
    st.warning(f"未在 `{strategies_dir()}` 发现策略。")
    st.stop()


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


# Honor any prefilled values set by 策略管理's "运行" shortcut.
preset = st.session_state.pop("_redirect_backtest", None)

icol1, icol2, icol3, icol4 = st.columns(4)
strategy_id = icol1.selectbox(
    "策略",
    options=list(registry.keys()),
    index=list(registry.keys()).index(preset["strategy_id"])
    if preset and preset.get("strategy_id") in registry else 0,
)
default_start = date.fromisoformat(preset["start"]) if preset else date.today().replace(year=date.today().year - 1)
default_end = date.fromisoformat(preset["end"]) if preset else date.today()
start = icol2.date_input("开始", value=default_start)
end = icol3.date_input("结束", value=default_end)
initial_cash = icol4.number_input(
    "初始现金 (¥)",
    value=float(preset["initial_cash"]) if preset else 100_000.0,
    min_value=1_000.0,
    step=10_000.0,
)

# Universe — comma-separated. Default: 510300,513500,511010 (ETF trio
# matches the etf_rebalance reference strategy; user can edit per run).
default_universe = "510300,513500,511010"
universe_text = st.text_input(
    "股票池(逗号分隔)",
    value=default_universe,
    help="策略的 target_position 输出只能包含这些代码。",
)
universe = [s.strip() for s in universe_text.split(",") if s.strip()]

benchmark = st.text_input("基准 (默认 000300 沪深300)", value="000300")

if start >= end:
    st.error("开始日期必须早于结束日期。")
    st.stop()
if not universe:
    st.error("请填写股票池。")
    st.stop()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


run_clicked = st.button("运行回测", type="primary")
result_holder: dict[str, Any] = {}

if run_clicked:
    adapter = get_adapter()
    progress = st.progress(0.0, text="准备中…")
    try:
        progress.progress(0.1, text="拉取交易日历…")
        try:
            calendar = adapter.get_calendar(start, end)
        except (DataAdapterUnavailable, EmptyBarsError, UnknownSymbolError) as exc:
            st.error(f"交易日历拉取失败:{type(exc).__name__}: {exc}")
            st.stop()
        if not calendar:
            st.error("该时间窗口内没有交易日。")
            st.stop()

        progress.progress(0.3, text="实例化策略…")
        cls = registry[strategy_id]
        # Prefer params from disk if present; otherwise default-construct.
        params_file = load_params(strategies_dir(), strategy_id)
        if params_file and isinstance(params_file.get("params"), dict):
            instance = cls(**params_file["params"])
        else:
            instance = cls()

        progress.progress(0.5, text="事件循环运行中…")
        engine = Engine(
            strategy=instance,
            universe=universe,
            adapter=adapter,
            benchmark_adapter=adapter,
            benchmark_symbol=benchmark.strip() or "000300",
            calendar=calendar,
            config=EngineConfig(),
        )
        equity = engine.run(initial_cash=float(initial_cash))

        progress.progress(0.9, text="写入历史…")
        # Parquet dir lives under data/ so deploy (T7) can mount it on a
        # persistent volume.
        parquet_dir = Path("data/backtests")
        run_id = save_run(
            get_connection(),
            equity,
            metrics_compute(equity),
            strategy_id=strategy_id,
            parquet_dir=parquet_dir,
        )
        result_holder["equity"] = equity
        result_holder["run_id"] = run_id
        progress.progress(1.0, text="完成。")
    except Exception as exc:
        st.error(f"回测失败:{type(exc).__name__}: {exc}")
        st.stop()


# ---------------------------------------------------------------------------
# Render result (also kept after rerun via cache: pull latest for the
# strategy that ran today if no in-memory result is available).
# ---------------------------------------------------------------------------


def _render(equity, metrics: dict, prior_metrics: dict | None) -> None:
    # --- PnL curve ---
    dates = [d.isoformat() for d in equity.dates]
    pv = [float(v) for v in equity.portfolio_value]
    bench = [float(v) for v in equity.benchmark_value]
    line = (
        Line(init_opts=opts.InitOpts(width="100%", height="380px"))
        .add_xaxis(dates)
        .add_yaxis(
            "策略净值",
            pv,
            is_smooth=True,
            label_opts=opts.LabelOpts(is_show=False),
            areastyle_opts=opts.AreaStyleOpts(opacity=0.18),
        )
        .add_yaxis(
            f"基准 {benchmark}",
            bench,
            is_smooth=True,
            label_opts=opts.LabelOpts(is_show=False),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="PnL 曲线", pos_left="center"),
            xaxis_opts=opts.AxisOpts(type_="category"),
            yaxis_opts=opts.AxisOpts(is_scale=True),
            tooltip_opts=opts.TooltipOpts(trigger="axis"),
            legend_opts=opts.LegendOpts(pos_top="3%"),
            datazoom_opts=[opts.DataZoomOpts(type_="inside")],
        )
    )
    st_pyecharts(line, height="380px")

    # --- Metric cards (six headline; win_rate & profit_loss_ratio surface too) ---
    def _delta(curr: float, prev: float | None) -> str | None:
        if prev is None:
            return None
        return f"{curr - prev:+.4f}"

    def _fmt_pct(x: float) -> str:
        return f"{x * 100:.2f}%"

    cols = st.columns(7)
    prior = prior_metrics or {}
    cols[0].metric("总收益率", _fmt_pct(metrics["total_return"]), _delta(metrics["total_return"], prior.get("total_return")))
    cols[1].metric("年化", _fmt_pct(metrics["annualized"]), _delta(metrics["annualized"], prior.get("annualized")))
    cols[2].metric("Sharpe", f"{metrics['sharpe']:.2f}", _delta(metrics["sharpe"], prior.get("sharpe")))
    cols[3].metric("最大回撤", _fmt_pct(metrics["max_drawdown"]), _delta(metrics["max_drawdown"], prior.get("max_drawdown")))
    cols[4].metric("Calmar", f"{metrics['calmar']:.2f}", _delta(metrics['calmar'], prior.get("calmar")))
    cols[5].metric("胜率", _fmt_pct(metrics["win_rate"]), _delta(metrics["win_rate"], prior.get("win_rate")))
    cols[6].metric("盈亏比", f"{metrics['profit_loss_ratio']:.2f}", _delta(metrics["profit_loss_ratio"], prior.get("profit_loss_ratio")))

    # --- Fills ---
    st.subheader("成交记录")
    if not equity.fills:
        st.caption("本次回测无成交。")
    else:
        df_fills = pd.DataFrame([
            {
                "日期": f.date.isoformat(),
                "代码": f.symbol,
                "方向": f.side,
                "数量": f.qty,
                "成交价": f.price,
                "手续费": f.fee,
            }
            for f in equity.fills
        ])
        st.dataframe(df_fills, use_container_width=True, hide_index=True)


if "equity" in result_holder:
    equity = result_holder["equity"]
    metrics = metrics_compute(equity)
    # Prior run for delta — second-most-recent row for this strategy.
    prior_row = get_connection().execute(
        "SELECT metrics_json FROM backtests "
        "WHERE strategy_id = ? AND id < ? ORDER BY ran_at DESC LIMIT 1",
        (strategy_id, result_holder["run_id"]),
    ).fetchone()
    prior_metrics = json.loads(prior_row[0]) if prior_row else None
    _render(equity, metrics, prior_metrics)
    st.success(f"✅ 回测完成 · 已写入 backtests#{result_holder['run_id']}")
else:
    # No new run — show the most recent one for this strategy if it exists.
    last_row = get_connection().execute(
        "SELECT id, metrics_json, ran_at, equity_path FROM backtests "
        "WHERE strategy_id = ? ORDER BY ran_at DESC LIMIT 1",
        (strategy_id,),
    ).fetchone()
    if last_row is not None:
        st.caption(f"最近一次回测: #{last_row[0]} @ {last_row[2]}")
        metrics = json.loads(last_row[1])
        # Re-render only the metrics card row + the parquet chart; we don't
        # have the in-memory equity so the fills table is omitted.
        try:
            df_eq = pd.read_parquet(last_row[3])
            line = (
                Line(init_opts=opts.InitOpts(width="100%", height="380px"))
                .add_xaxis(df_eq["date"].astype(str).tolist())
                .add_yaxis(
                    "策略净值",
                    df_eq["portfolio_value"].astype(float).tolist(),
                    is_smooth=True,
                    areastyle_opts=opts.AreaStyleOpts(opacity=0.18),
                )
                .add_yaxis(
                    f"基准 {benchmark}",
                    df_eq["benchmark_value"].astype(float).tolist(),
                    is_smooth=True,
                )
                .set_global_opts(
                    title_opts=opts.TitleOpts(title="PnL 曲线(历史)", pos_left="center"),
                    xaxis_opts=opts.AxisOpts(type_="category"),
                    yaxis_opts=opts.AxisOpts(is_scale=True),
                    legend_opts=opts.LegendOpts(pos_top="3%"),
                    datazoom_opts=[opts.DataZoomOpts(type_="inside")],
                )
            )
            st_pyecharts(line, height="380px")
        except Exception as exc:
            st.caption(f"无法读取历史净值:{exc}")

        cols = st.columns(7)
        cols[0].metric("总收益率", f"{metrics['total_return'] * 100:.2f}%")
        cols[1].metric("年化", f"{metrics['annualized'] * 100:.2f}%")
        cols[2].metric("Sharpe", f"{metrics['sharpe']:.2f}")
        cols[3].metric("最大回撤", f"{metrics['max_drawdown'] * 100:.2f}%")
        cols[4].metric("Calmar", f"{metrics['calmar']:.2f}")
        cols[5].metric("胜率", f"{metrics['win_rate'] * 100:.2f}%")
        cols[6].metric("盈亏比", f"{metrics['profit_loss_ratio']:.2f}")
    else:
        st.caption("尚无回测记录。点击「运行回测」开始。")
