# T10 — K 线 + 买卖点叠加 图表方案对比

> 调研日期：2026-08-22
> 范围：Python 侧 A 股 K 线 + 买卖点标注 图表方案，浏览器访问。
> 候选：Pyecharts、Plotly、mplfinance + backend、ECharts 原生 JS。

---

## 1. Feature Matrix

评分约定：5 = 优秀 / 原生支持，4 = 良好 / 一行调用，3 = 可用，2 = 麻烦，1 = 不支持或极差。

| 维度 | Pyecharts (v2.1.0, ECharts 6) | Plotly (py 6.x / plotly.js 3.7) | mplfinance + mpld3 | ECharts 原生 JS |
|---|---|---|---|---|
| **K 线（candlestick）支持** | 5 — `Kline` 类，原生 OHLC | 5 — `go.Candlestick`，含 OHLC | 5 — `mpf.plot(type='candle')` | 5 — `series.type='candlestick'` |
| **A 股复权（颜色：红涨绿跌）** | 4 — `itemstyle_opts(color='#ec0000', color0='#00da3c')` | 4 — `increasing.line_color='#ec0000'` | 4 — `make_marketcolors(up='r', down='g')` | 5 — `itemStyle.color/color0` 直接写 |
| **叠加买卖点 + 文字 + 颜色** | 5 — `MarkPointItem(coord, value, itemstyle_opts={'color': ...})`，`label_opts=LabelOpts` | 4 — `go.Scatter(mode='markers+text', marker=dict(symbol, color, size))` + `fig.add_annotation` | 4 — `make_addplot(type='scatter', marker='^'/'v', color)`；标签要靠 `ax.text()` 二次绘制 | 5 — `markPoint.data` + `itemStyle` + `label.formatter` 最灵活 |
| **缩放 / 拖拽 / 十字光标** | 5 — `datazoom_opts(inside+slider)`、`axispointer_opts(link=[{"xAxisIndex": "all"}])` | 5 — `xaxis_rangeslider_visible`、`rangeselector`、内置 crosshair | 1 — mpld3 极有限；默认静态 | 5 — `dataZoom`/`brush`/`axisPointer.link` 全套 |
| **多图联动（K线 + 量 + 指标）** | 5 — `Grid` 垂直堆叠 + `xaxis_index` 共享 + `axispointer.link='all'` | 4 — `make_subplots(rows, shared_xaxes=True, row_heights)`，cross-pane 联动需手动设 `xaxis` 引用 | 4 — `panel=` + `volume=True` + `secondary_y='auto'` | 5 — 多个 grid + `xAxis.pointer.link` 字段 |
| **Streamlit 集成** | 4 — `streamlit-echarts[pyecharts]` 提供 `st_pyecharts()`，本质 iframe | 5 — `st.plotly_chart(fig, use_container_width=True)` 一行 | 3 — `st.pyplot(fig)` 或 `mpld3.fig_to_html` + `st.components.v1.html` | 1 — 必须自己写 React/HTML 包裹层 |
| **FastAPI / 纯 HTML 集成** | 5 — `chart.render("x.html")` 输出独立 HTML，内嵌 ECharts CDN | 5 — `fig.write_html("x.html", include_plotlyjs="cdn")` 一键 | 3 — mpld3 输出 HTML，但样式与现有 UI 难统一 | 5 — 直接产出 HTML 字符串，最干净 |
| **包体积**（minified + gzip） | ~340 KB（echarts.min.js 主包） | ~400 KB（plotly.js `finance` partial） | mpld3 本身 ~80 KB，但依赖 matplotlib 全家桶 | ~340 KB（echarts.min.js 主包） |
| **首屏加载**（本地回环） | < 1s（单文件 + CDN fallback） | < 1s（`include_plotlyjs='cdn'`） | < 1s，但绘图本身慢（matplotlib 渲染） | < 1s |
| **OHLC hover tooltip** | 4 — `tooltip_opts(trigger='axis', axis_pointer_type='cross')` 自动 OHLC | 5 — hover 自动显示 OHLC + 自定义字段 | 1 — 静态图，hover 能力依赖 mpld3 | 5 — `tooltip.formatter` JS 函数完全自定义 |
| **rangeSelector / 1D 1W 1M** | 3 — 用 `datazoom` slider 手搓，无原生 button 切换 | 5 — `xaxis_rangeselector=dict(buttons=[...])` 一行 | 1 — 没有 | 4 — 用 `toolbox.feature.dataZoom` + 自定义 button |

---

## 2. Code Snippets — Top 2 Candidates

> 目标：一张 K 线 + 5 个买点 + 3 个卖点。所有数据用 numpy 生成，自包含可运行。
> A 股惯例：**红涨绿跌**。

### 2.1 Pyecharts（推荐主力方案）

```python
"""
kline_demo_pyecharts.py — K 线 + 5 买点 + 3 卖点
依赖：pip install pyecharts==2.0.9   # ECharts 5.x 稳定线
     # 或 pyecharts==2.1.0（ECharts 6）
"""
import numpy as np
import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, Kline, Line

def gen_ohlcv(days: int = 250, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=days, freq="B")
    ret = rng.normal(0.0008, 0.018, days)
    close = 100 * np.cumprod(1 + ret)
    open_ = close * (1 + rng.uniform(-0.012, 0.012, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.010, days)))
    low  = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.010, days)))
    vol  = rng.integers(1_000_000, 12_000_000, days)
    return pd.DataFrame({"date": dates, "open": open_, "close": close,
                         "high": high, "low": low, "volume": vol})

def build_chart(df: pd.DataFrame) -> Grid:
    x = df["date"].dt.strftime("%Y-%m-%d").tolist()
    ohlc = df[["open", "close", "low", "high"]].round(2).values.tolist()

    # 5 买点 + 3 卖点（A 股：买=红↑、卖=绿↓）
    buy_idx  = [20, 55, 90, 140, 200]
    sell_idx = [40, 110, 180]
    mp_items = (
        [opts.MarkPointItem(name="买", coord=[x[i], df["low"].iloc[i] * 0.985],
                            value="买", symbol="triangle",
                            itemstyle_opts=opts.ItemStyleOpts(color="#ec0000"))
         for i in buy_idx] +
        [opts.MarkPointItem(name="卖", coord=[x[i], df["high"].iloc[i] * 1.015],
                            value="卖", symbol="triangle",
                            symbol_rotate=180,
                            itemstyle_opts=opts.ItemStyleOpts(color="#14b143"))
         for i in sell_idx]
    )

    kline = (
        Kline(init_opts=opts.InitOpts(width="1400px", height="600px",
                                      theme="dark"))
        .add_xaxis(x)
        .add_yaxis(
            series_name="K 线",
            y_axis=ohlc,
            itemstyle_opts=opts.ItemStyleOpts(
                color="#ec0000", color0="#14b143",
                border_color="#8A0000", border_color0="#065726",
            ),
            markpoint_opts=opts.MarkPointOpts(
                data=mp_items,
                symbol_size=42,
                label_opts=opts.LabelOpts(color="#fff", font_size=11,
                                          position="inside"),
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="A 股 K 线 + 买卖点", pos_left="center"),
            legend_opts=opts.LegendOpts(pos_top="3%"),
            xaxis_opts=opts.AxisOpts(
                type_="category", is_scale=True, boundary_gap=False,
                axispointer_opts=opts.AxisPointerOpts(is_show=True),
            ),
            yaxis_opts=opts.AxisOpts(
                is_scale=True,
                axispointer_opts=opts.AxisPointerOpts(is_show=True),
            ),
            tooltip_opts=opts.TooltipOpts(
                trigger="axis", axis_pointer_type="cross",
                background_color="rgba(40,40,40,0.85)",
                border_color="#333",
                textstyle_opts=opts.TextStyleOpts(color="#fff"),
            ),
            datazoom_opts=[
                opts.DataZoomOpts(type_="inside",  xaxis_index=[0, 1], range_start=70, range_end=100),
                opts.DataZoomOpts(type_="slider",  xaxis_index=[0, 1], pos_bottom="4%",
                                  range_start=70, range_end=100),
            ],
            axispointer_opts=opts.AxisPointerOpts(
                is_show=True, link=[{"xAxisIndex": "all"}],
                label=opts.LabelOpts(background_color="#777"),
            ),
        )
    )

    # 成交量副图（按涨跌染色）
    vol_colors = ["#ec0000" if c >= o else "#14b143"
                  for o, c in zip(df["open"], df["close"])]
    volume = (
        Bar()
        .add_xaxis(x)
        .add_yaxis(
            "成交量", df["volume"].tolist(),
            xaxis_index=1, yaxis_index=1,
            itemstyle_opts=opts.ItemStyleOpts(color=vol_colors),
            label_opts=opts.LabelOpts(is_show=False),
        )
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(type_="category", grid_index=1,
                                     axislabel_opts=opts.LabelOpts(is_show=False)),
            yaxis_opts=opts.AxisOpts(grid_index=1, split_number=2, name="量"),
            legend_opts=opts.LegendOpts(pos_top="70%"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="shadow"),
        )
    )

    return (
        Grid(init_opts=opts.InitOpts(width="1400px", height="600px", theme="dark"))
        .add(kline,  grid_opts=opts.GridOpts(pos_left="8%", pos_right="8%",
                                             pos_top="6%",  height="60%"))
        .add(volume, grid_opts=opts.GridOpts(pos_left="8%", pos_right="8%",
                                             pos_top="74%", height="18%"))
    )

if __name__ == "__main__":
    df = gen_ohlcv()
    chart = build_chart(df)
    chart.render("kline_pyecharts.html")
    print("OK -> kline_pyecharts.html")
```

**Streamlit 集成**（额外一行）：

```python
# streamlit_app.py
import streamlit as st
from streamlit_echarts import st_pyecharts          # pip install streamlit-echarts[pyecharts]
from kline_demo_pyecharts import gen_ohlcv, build_chart

st.title("股票 K 线 Demo")
df = gen_ohlcv()
st_pyecharts(build_chart(df), height="640px")
```

### 2.2 Plotly（备选 / 已有 Plotly 经验时）

```python
"""
kline_demo_plotly.py — K 线 + 5 买点 + 3 卖点
依赖：pip install plotly
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def gen_ohlcv(days: int = 250, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=days, freq="B")
    ret = rng.normal(0.0008, 0.018, days)
    close = 100 * np.cumprod(1 + ret)
    open_ = close * (1 + rng.uniform(-0.012, 0.012, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.010, days)))
    low  = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.010, days)))
    vol  = rng.integers(1_000_000, 12_000_000, days)
    return pd.DataFrame({"date": dates, "open": open_, "close": close,
                         "high": high, "low": low, "volume": vol})

def build_chart(df: pd.DataFrame) -> go.Figure:
    buy_idx  = [20, 55, 90, 140, 200]
    sell_idx = [40, 110, 180]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=[0.72, 0.28],
    )

    # K 线（A 股红涨绿跌）
    fig.add_trace(go.Candlestick(
        x=df["date"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="K 线",
        increasing=dict(line=dict(color="#ec0000"), fillcolor="#ec0000"),
        decreasing=dict(line=dict(color="#14b143"), fillcolor="#14b143"),
    ), row=1, col=1)

    # 买点（红三角，置于低点下方）
    fig.add_trace(go.Scatter(
        x=df["date"].iloc[buy_idx], y=df["low"].iloc[buy_idx] * 0.985,
        mode="markers+text", name="买点",
        marker=dict(symbol="triangle-up", size=14, color="#ec0000",
                    line=dict(color="#fff", width=1)),
        text=["买"] * len(buy_idx), textposition="bottom center",
        textfont=dict(color="#ec0000", size=12),
    ), row=1, col=1)

    # 卖点（绿倒三角，置于高点上方）
    fig.add_trace(go.Scatter(
        x=df["date"].iloc[sell_idx], y=df["high"].iloc[sell_idx] * 1.015,
        mode="markers+text", name="卖点",
        marker=dict(symbol="triangle-down", size=14, color="#14b143",
                    line=dict(color="#fff", width=1)),
        text=["卖"] * len(sell_idx), textposition="top center",
        textfont=dict(color="#14b143", size=12),
    ), row=1, col=1)

    # 成交量副图（按涨跌染色）
    vol_colors = ["#ec0000" if c >= o else "#14b143"
                  for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(
        x=df["date"], y=df["volume"], name="成交量",
        marker_color=vol_colors, showlegend=False,
    ), row=2, col=1)

    fig.update_layout(
        title=dict(text="A 股 K 线 + 买卖点", x=0.5),
        xaxis_rangeslider_visible=False,        # 关闭下方 rangeslider
        xaxis2_rangeslider_visible=False,
        template="plotly_dark", height=620,
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
        hovermode="x unified",
    )
    # 双轴十字光标联动
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor",
                     spikecolor="#888", spikethickness=1)
    return fig

if __name__ == "__main__":
    df = gen_ohlcv()
    fig = build_chart(df)
    fig.write_html("kline_plotly.html", include_plotlyjs="cdn")
    fig.show()
    print("OK -> kline_plotly.html")
```

**Streamlit 集成**（不需要第三方组件）：

```python
import streamlit as st
from kline_demo_plotly import gen_ohlcv, build_chart

st.plotly_chart(build_chart(gen_ohlcv()), use_container_width=True)
```

---

## 3. Interaction Quality（trader-friendly UX）

| 体验项 | Pyecharts | Plotly | mplfinance+mpld3 | ECharts 原生 |
|---|---|---|---|---|
| 十字光标 + OHLC tooltip | 4 — `axis_pointer_type='cross'` + 自定义 formatter | 5 — hovermode='x unified' 自动聚合 OHLC+成交量 | 1 — 没有 | 5 — `tooltip.formatter` 完全自定义 |
| 时间区间快速切换（1D/1W/1M/全部） | 3 — 用 slider 手搓 | 5 — `rangeselector.buttons=[…]` 一行 | 1 — 没有 | 4 — `toolbox.feature.dataZoom` + 自定义按钮 |
| 框选放大（brush） | 5 — `brush_opts(brush_type='lineX')` | 3 — 需 `dragmode='select'` + 自定义回调 | 1 — 没有 | 5 — `brush` 组件全套 |
| 鼠标滚轮缩放 | 5 — `datazoom_opts(type_='inside')` | 5 — 内置 | 1 — 没有 | 5 — 内置 |
| 双图联动（K 线 ↔ 成交量同坐标） | 5 — `axispointer_opts(link='all')` | 4 — `make_subplots(shared_xaxes=True)` 自动联动 X 轴 | 4 — mplfinance 自动 | 5 — `xAxis.pointer.link` |
| 暗色主题 | 5 — `theme='dark'` 30+ 主题 | 4 — `template='plotly_dark'` | 3 — `make_mpf_style` 改名 | 5 — theme object |
| 移动端触屏 | 5 — touch 事件 | 4 — 支持 | 1 — 没有 | 5 — 支持 |

**结论**：Pyecharts 与 Plotly 在交互体验上**几乎并列第一**；Plotly 的 `rangeselector` 比 Pyecharts 强一点，但 Pyecharts 的 brush/双图联动更省事。

---

## 4. Gotchas（每个库的常见坑）

### Pyecharts
1. **数据顺序是 `[open, close, low, high]`**，不是标准的 `[o, h, l, c]`。传错会画成奇怪的"反向 K 线"。
2. **`MarkPointItem(coord=[x, y])`** 的 `x` 必须是 x 轴上已存在的字符串（日期），数字索引不行；坐标会落在最近一个分类点上。
3. **`MarkPointOpts` v2 重构过**（#2427）：旧代码里 `symbol_size=[100,30]` 这种尺寸语法变了，参考 [2.0.9 release notes](https://github.com/pyecharts/pyecharts/releases/tag/v2.0.4)。
4. **多图联动**必须在每个子图上重复写 `axispointer_opts(link=[{"xAxisIndex": "all"}])`，并且 `xaxis_index=[0,1]` 共享坐标；少一处联动就断。
5. **版本切换**：v2.0.9 之后是 ECharts 6（部分 API 改名），混用文档容易踩坑——锁版本。
6. **`render()` 输出独立 HTML**；如果想在 Streamlit 里嵌必须走 `streamlit-echarts` 或 `st.components.v1.html`。

### Plotly
1. **K 线 `xaxis_rangeslider_visible` 默认是 True**，副图 `xaxis2_rangeslider_visible` 也得手动关，否则下面多一条灰条。
2. **多图 K 线 hover 不会自动联动**——必须设 `hovermode='x unified'` + 显式 `fig.update_xaxes(showspikes=True)`。
3. **中文字体**要手动指定 `fig.update_layout(font=dict(family="Microsoft YaHei"))`，否则 label 渲染成方框。
4. **A 股颜色**：`increasing.line_color` 与 `fillcolor` 都要单独设（默认只改 line，fill 还是绿）。
5. **首次加载包大**：默认 `write_html` 会内嵌整份 plotly.js（3+ MB）；一定要传 `include_plotlyjs='cdn'`。
6. **Streamlit 下 `fig.show()` 无效**——必须用 `st.plotly_chart(fig)`，否则浏览器一片白。

### mplfinance + mpld3
1. **mpld3 渲染 SVG，>2000 根 K 线就明显卡**——这是 D3 SVG 的硬限，不是 mpld3 bug。
2. **`make_addplot` 序列必须与主 OHLC 的 DatetimeIndex 完全对齐**，否则 marker 飞到画外；用 `np.nan` 补齐非信号日。
3. **三角形 `marker='^'/'v'` 的 markersize 与 matplotlib 像素概念不同**，常用 100~200 才看得清。
4. **mpld3 已基本停止维护**（2018 后少更新），Numpy 序列化等 issue 多年没合。
5. **静态图**——没有 crosshair、没有框选、没有 rangeselector。技术图表够，trader 习惯的 Web 交互不够。
6. **副图比例**只能 `panel_ratios=[3,1]`，精细调整要靠 `figscale` + 后处理。

### ECharts 原生 JS
1. **必须前端集成**——浏览器传 `option` JSON 对象；Python 侧只能输出 dict。
2. **`markPoint.data` 单点格式**：`{coord: [x, y], value: 2300, itemStyle: {color: 'red'}}`；注意 `coord` 用类目轴时 `x` 必须是字符串。
3. **跨主题暗色**要自己写 theme 对象或 `option.backgroundColor`；ECharts 没有内建 matplotlib-式的 stylesheet。
4. **打包到 Streamlit**——必须自己写 `st.components.v1.html(...)`，且要手动注入 ECharts CDN 脚本。
5. **Python 侧做不了类型校验**——一个 typo（比如 `markPoint` 写成 `markpoint`）运行时才报错。

---

## 5. Recommendation

**主选 Pyecharts (v2.0.9 稳定线)，备选 Plotly**。理由按我们的约束逐条对照：

| 我们的约束 | 满足情况 |
|---|---|
| 单 Python 进程 | ✅ Pyecharts 与 Plotly 都纯 Python，输出 HTML，无 Node。 |
| 浏览器访问 | ✅ `chart.render("x.html")` 或 `fig.write_html()` 直接静态文件；FastAPI 一行挂载。 |
| 极简 — 不需要极端交互 | ✅ K 线 + 买卖点 + 量联动已覆盖 100%；不需要 Plotly 的 `rangeselector` 高级按钮。 |
| A 股 K 线 + 买卖点原生友好 | ✅ Pyecharts 中文文档、`MarkPointItem(coord, value, itemstyle_opts)` 一行搞定买卖点 + 自定义颜色 + 文字标签；多图 Grid 联动一行 `axispointer_opts(link='all')`。 |
| Streamlit 集成成本 | ✅ Plotly `st.plotly_chart` 一行；Pyecharts 走 `streamlit-echarts[pyecharts]` 的 `st_pyecharts()` 也是一行。持平。 |
| 包体积 / 首屏 | ✅ Pyecharts 主包 ~340 KB gzip（与 Plotly finance partial 400 KB 持平），首次加载无差异。 |
| 工程成熟度（中文社区） | ✅ Pyecharts 在 A 股量化圈是默认选项，CSDN/掘金/知乎示例大量。 |

**为什么不选 ECharts 原生 JS**：要前端集成层，破坏"单 Python 进程"约束，除非我们已经有 React/Vue 工作流。

**为什么不选 Plotly**：交互略胜但 A 股配色、K 线 + 量 + 副图联动要写更多代码；Plotly 的真正杀手锏是 `rangeselector`/`dragmode` 这类我们不需要的能力。

**为什么不选 mplfinance + mpld3**：静态图为主，trader 习惯的 crosshair/框选/滚轮缩放全缺；不适合"浏览器访问"这个核心交付形态。

### 最终推荐组合（写入 MAP.md）

| 组件 | 选型 | 锁定版本 |
|---|---|---|
| 主图表 | **Pyecharts** | `pyecharts==2.0.9`（ECharts 5 稳定线，2025-10 最后支持版本） |
| Streamlit 桥 | **streamlit-echarts[pyecharts]** | `streamlit-echarts>=0.4.0` |
| FastAPI 桥 | 直接 `chart.render("x.html")` 静态托管 | — |
| Plotly 备用 | 仅当 Pyecharts 某能力缺失时引入 | `plotly>=6.0` |

### 后续动作（不在本 ticket 范围）

- 把买卖点的 `MarkPointItem` 封装成一个工具函数 `add_buy_sell_marks(kline, buy_df, sell_df)`，统一颜色 `#ec0000/#14b143` 与偏移规则。
- A 股复权：默认前复权，数据侧统一在 AKShare 拉取时处理（见 T1/T4），不放在 chart 层。
- 主题：先用 `theme='dark'` 出 demo，再考虑写一个"wayfinder"自定义 theme 对象注入品牌色。

---

## Appendix：版本与来源

- Pyecharts 2.0.9 release（2025-10-10，最后 ECharts 5 版本）：[GitHub](https://github.com/pyecharts/pyecharts/releases)
- Pyecharts 2.1.0（2026-02，ECharts 6）：同上
- Pyecharts gallery — professional_kline_chart：[gallery.pyecharts.org](http://gallery.pyecharts.org/#/Candlestick/professional_kline_chart)
- Pyecharts MarkPoint 买卖点中文教程：[CSDN — halps](https://blog.csdn.net/halps/article/details/127095408)、[GitCode — 上下分开展示](https://blog.gitcode.com/9a2bcb1e42fb659eaafacdd41f04ec19.html)
- Plotly candlestick 官方：[plotly.com/python/candlestick-charts](https://plotly.com/python/candlestick-charts/)
- Plotly subplots 官方：[plotly.com/python/subplots](https://plotly.com/python/subplots/)
- Plotly.js bundle sizes (v3.7.0)：[plotly/plotly.js dist/README](https://github.com/plotly/plotly.js/blob/master/dist/README.md) — `finance` partial 400 KB gzip
- mplfinance make_addplot：[DeepWiki reference](https://deepwiki.com/matplotlib/mplfinance/6.1-additional-plots-with-make_addplot())
- mpld3 limitations：[mpld3 FAQ](https://mpld3.github.io/faq.html)
- streamlit-echarts 0.7.0：[PyPI](https://pypi.org/project/streamlit-echarts/)、[GitHub](https://github.com/andfanilo/streamlit-echarts)
- ECharts markPoint：[Apache ECharts option docs](https://echarts.apache.org/en/option.html)、[Mintlify component ref](https://mintlify.wiki/apache/echarts/components/mark-point)
- 2025-2026 综合对比：[腾讯云 — K线库对比](https://cloud.tencent.cn/developer/article/2659243)、[掘金 — 量化K线](https://juejin.cn/post/7528753424775888922)、[CSDN — Python量化股票K线](https://blog.csdn.net/likuoelie/article/details/153458841)
