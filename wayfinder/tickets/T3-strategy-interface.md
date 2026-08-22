---
name: T3-strategy-interface
title: 策略接口设计
type: grilling
status: closed
claimed-by: wayfinder-session-1
blocked-by: [T2 ✅]
closed-date: 2026-08-22
---

## Resolution

**决策（4 个子决策全部确定）：**

### 1. 信号格式：target_position

策略返回 `{symbol: target_weight}`（0-1 百分比）。框架算 diff → 下单。

**契合场景**：用户描述的「发工资买入 + 跌多买回 + 涨多减仓」就是教科书式的 target_weight 再平衡。一行策略代码：

```python
return {"510300": 0.4, "513500": 0.3, "511010": 0.3}
```

### 2. Context 对象（策略能查的所有东西）

```python
class Context:
    now: date
    universe: list[str]
    bars(symbol, lookback) -> DataFrame  # OHLCV + 复权
    positions: dict[str, Position]      # 当前虚拟持仓 {symbol: {qty, avg_cost, opened_at}}
    cash: float                          # 当前虚拟现金
    portfolio_value: float               # 持仓市值 + 现金
    trades: list[Trade]                  # 当前虚拟账本的所有成交
    state: dict                          # 策略专属 state.json
    price(symbol) -> float               # 当前价（便捷方法）
```

### 3. 状态管理：默认无状态 + state.json 可选

- 大多数策略是纯函数（不读 ctx.state）
- 需要持久化时（如止损位、冷却期）：写 `ctx.state["x"] = ...`，框架自动保存到 `strategies/<name>.state.json`
- 服务重启不丢状态
- 重置策略 = 删 state.json

### 4. 加载机制：自动扫描 strategies/ 目录

- 启动时扫描 `strategies/*.py`，import 每个
- 有 `name` 类属性 + 继承 `Strategy` 基类的类 → 自动注册
- UI 下拉自动出现
- 加新策略 = 加文件，重启或点刷新按钮

### 5. 参数配置：`__init__` 默认参数 + UI 自动表单

```python
class ETF再平衡(Strategy):
    name = "ETF 目标权重再平衡"
    
    def __init__(self, drift_threshold: float = 0.05, rebalance_freq: str = "weekly"):
        self.drift_threshold = drift_threshold
        self.rebalance_freq = rebalance_freq
    
    def generate(self, ctx):
        return self.targets
```

- 类型注解 → Streamlit UI 自动生成对应表单控件（float→slider, str→selectbox, int→number_input）
- 用户在 UI 改参数 → 实例化新对象 → 重新 generate
- 参数持久化：写到 `strategies/<name>.params.json`

---

## Question

策略作为独立模块加载运行，它的边界怎么划？

需要回答的子问题：

1. **输入**：策略接收什么数据？`pd.DataFrame[date, OHLCV, factors]`？是否包含财务/北向等派生字段？
2. **输出**：信号格式是什么？三选一或自定义：
   - **target_position**：每个股票每天的目标仓位（如 `target=0.5` 表示占 50% 资金）
   - **signal tuple**：`(action, symbol, price_hint, confidence)` 元组
   - **order**：`Order(symbol, side, qty, price, reason)`
3. **状态**：策略是否需要跨日状态（如持仓成本、止盈止损位）？如何持久化？
4. **参数**：策略参数怎么暴露？JSON 配置文件？UI 表单？环境变量？
5. **加载方式**：UI 上下拉选？还是扫描 `strategies/` 目录自动注册？

约束：
- 策略代码不依赖框架具体实现（解耦）—— 应该是 import 一个 Protocol + 一个 context 对象就够了
- 支持热切换（不重启服务就能换策略）
- 一个策略出问题不影响其他策略

建议流程：先 domain-modeling 把"信号"和"策略上下文"两个核心概念锁死。
