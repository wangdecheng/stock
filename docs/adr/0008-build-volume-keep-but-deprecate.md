# `build_volume` 保留并标 deprecated

股票详情页(`pages/1_股票详情.py`)本轮把成交量副图从 panel 布局里去掉(见 ADR-0006),改用 K 线 + ADX + RSI 三 panel。但 `framework/charts/__init__.py` 的 `build_volume` 公开 API 仍然保留,在 docstring 标 deprecated。

## Considered Options

- **方案 A 保留并标 deprecated**(本方案):`build_volume` 留在 `framework/charts/__init__.py`,`__all__` 不下线,docstring 标注 "currently unused by stock detail — kept for potential re-use on backtest page",`pages/1_股票详情.py` 不再调用。
- **方案 B 完全下线**:从 `__all__` 移除,删实现,删 `tests/test_charts.py` 里的相关测试。回测页将来要再画成交量时得重写。

## Decision

采用 **方案 A**。

理由:

1. 用户原话是「成交量感觉暂时用不到」,**不是「永远不要」**。回测页(`pages/4_回测.py`)目前没画成交量,但权益曲线本身隐含了资金流向,加一个成交量底图是常见增强 —— 那时 `build_volume` 能直接复用,免去重写 Pyecharts Bar 双 series stack + 红绿着色的样板。
2. **公开 API 收缩的代价**:`framework/charts` 是其他三个 page 的公共依赖层,任何 `__all__` 里的名字删除都要追外部 import。docstring 标注 deprecated 是**零破坏**的提醒方式 —— 静态检查、`help()`、IDE hover 都能看到,真要删等回测页调研完一起删。
3. **测试保留**:`tests/test_charts.py` 里 `build_volume` 的颜色常量 / 红绿 series 分桶测试继续跑,作为「未来回测页启用时直接可用」的回归网。

## Consequences

- `framework/charts/__init__.py` 的 `build_volume` docstring 首行加一句 `Deprecated:` 标记,跟 Python 生态惯例一致。
- `pages/1_股票详情.py` 删除 `build_volume` 的间接调用(从 `build_kline_volume_grid` 改成新的 `build_indicator_grid`),不再 `import build_volume`。
- 若未来 12 个月内回测页仍未启用 `build_volume`,本 ADR 触发「彻底下线」复审 —— 即删实现 + 删测试 + 删 docstring 标注。
- 不引入 `warnings.warn(..., DeprecationWarning)`:那是给**用户主动调用**的情况准备的;本场景下只是「作者层不使用」,docstring 标注足矣,运行时噪音反而干扰图表调试。
