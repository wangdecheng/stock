# 08 — 提取 Backend 协议与 DataAdapter 门面(宽重构 · expand 阶段)

**What to build:**
现有 `framework.data.adapter.AKShareAdapter` 同时承担"名号 / 实现 / 门面"三种角色。本次只做**纯重构,零行为变更**:把 AKShare 实现搬到 `framework.data.backends.akshare.AKShareBackend`,新增 `Backend` 协议/抽象基类,把 `AKShareAdapter` 重命名为 `DataAdapter` 作为唯一门面。所有调用方零改动,所有现有测试全绿。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 新增 `framework.data.backends` 子包与 `__init__.py`,定义 `Backend` 协议(声明 `get_bars / get_fundamentals / get_calendar / ping`,签名与现有 AKShare 公开方法一致)
- [ ] `AKShareBackend` 持有原 `AKShareAdapter` 中除门面编排外的所有逻辑(`_fetch_bars` / `_normalize` / `get_fundamentals` / `get_calendar`),不再持有 `BarsResult` 编排
- [ ] 新门面 `DataAdapter` 持有 `_backends: dict[str, Backend]`(单 backend 启动时为 `{bars: AKShareBackend, fundamentals: AKShareBackend, calendar: AKShareBackend}`),公开 `get_bars / get_fundamentals / get_calendar`,行为与重构前一致
- [ ] 保留 `AKShareAdapter` 作为 `DataAdapter` 的别名以兼容旧 import(后续 ticket 09 之类的工作不期望破坏调用方)
- [ ] `app.py` / `pages/1_股票详情.py` / `pages/4_回测.py` / `framework/runner/*` / `framework/strategy/context.py` / 所有 tests 无需修改 import 即可跑通
- [ ] `pytest tests/test_data_layer.py tests/test_ui_t5.py tests/test_runner_t6.py tests/test_strategy_t2.py tests/test_backtest_t3.py` 全绿
- [ ] CI grep 墙扩展:`framework/data/backends/` 目录允许 `import akshare`,其他位置仍禁止(为后续 BaoStock 留口子,但本 ticket 不引入 baostock 依赖)