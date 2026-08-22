---
name: T2-backtest-engine
title: 回测引擎选定
type: grilling
status: closed
claimed-by: wayfinder-session-1
blocked-by: []
closed-date: 2026-08-22
---

## Resolution

**决策：自写最小引擎 + empyrical 算指标**

用户明确：「一个策略得测试才能运行」——回测是策略上线前的强制门槛。

实现路径：
1. `engine.py` — 事件循环 ~200 行
   - 逐日调策略 → 拿 `target_position` → 调 `broker.set_target()` → 按次日开盘价撮合
   - 记录每笔成交、每日净值、benchmark 对比
   - 输出：`Equity` 对象（时间序列净值 + 成交记录 + benchmark 对比）
2. `metrics.py` — 指标计算 ~50 行（封装 empyrical）
   - 总收益、年化、Sharpe、最大回撤、Calmar、胜率、盈亏比
   - 输入 `Equity`，输出 dict 给 UI
   - 注意：技术指标（ADX/MACD/MA/BBands 等）由 [T12](../tickets/T12-tech-indicators.md) 单独处理（pandas-ta），策略代码里通过 `indicators.xxx()` 调用
3. 整合到回测 UI：选策略 → 选时间窗口 → 选起始资金 → 看 PnL 曲线 + 指标卡片

约束：
- 不做撮合模拟精度（T+1、涨跌停、滑点都用简化假设）
- 单线程、单进程，足够个人用量
- 策略上线前强制走回测（不是建议，是硬约束）

---

## Question

回测引擎选哪个？候选：

- **自写最小引擎**：约 200 行 Python（按日迭代 → 调策略 → 算 PnL），完全可控、和策略接口契合
- **Backtesting.py**：纯 Python、API 干净、单文件几百行依赖；但和本案的"操作计划输出"不完全契合（它是为图形化回测设计的）
- **Rqalpha**：国内开源、A 股适配好、支持分钟级；但依赖较重、和策略接口需要适配层
- **VectorBT**：向量化、参数扫描快；但入门陡、文档偏少

约束：
- 回测不实盘，不需要撮合模拟的精度
- 重点是能跑通策略接口、生成 PnL 曲线
- 单机部署，不需要分布式

决策维度：
1. 与策略接口（T3）的契合度
2. 学习/适配成本
3. 长期维护成本
