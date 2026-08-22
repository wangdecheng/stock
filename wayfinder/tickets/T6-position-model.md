---
name: T6-position-model
title: 持仓模型设计
type: grilling
status: closed
claimed-by: wayfinder-session-1
blocked-by: []
closed-date: 2026-08-22
---

## Resolution

**决策：双轨（真实持仓 + 每策略虚拟账本）**

数据模型（SQLite，三张表）：

```sql
-- 真实持仓：用户手动录入的成交记录
CREATE TABLE real_trades (
  id INTEGER PRIMARY KEY,
  symbol TEXT NOT NULL,           -- 600519
  side TEXT NOT NULL,             -- buy / sell
  qty INTEGER NOT NULL,
  price REAL NOT NULL,
  fee REAL DEFAULT 0,
  executed_at DATE NOT NULL,
  note TEXT
);

-- 策略虚拟账本：每个策略有自己的现金 + 持仓视图
CREATE TABLE virtual_books (
  strategy_id TEXT PRIMARY KEY,   -- strategies/ma_cross.py → 'ma_cross'
  initial_cash REAL DEFAULT 100000,
  cash REAL NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 策略虚拟持仓
CREATE TABLE virtual_positions (
  id INTEGER PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  qty INTEGER NOT NULL,
  avg_cost REAL NOT NULL,         -- 加权平均成本
  opened_at DATE NOT NULL,
  FOREIGN KEY (strategy_id) REFERENCES virtual_books(strategy_id)
);

-- 策略每日建议（操作计划）
CREATE TABLE strategy_suggestions (
  id INTEGER PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  action TEXT NOT NULL,           -- buy / sell / hold
  target_qty INTEGER,             -- 目标持仓数量
  target_price REAL,              -- 建议价格
  confidence REAL,                -- 0-1
  reason TEXT,                    -- 策略输出的人话理由
  generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  applied BOOLEAN DEFAULT 0       -- 是否已应用到虚拟账本
);
```

UI 行为：
- 持仓管理页：`st.data_editor` 编辑真实成交表
- 仪表盘：真实持仓市值 + 当前激活策略的虚拟账本市值，两条曲线对比
- 策略跑批：写 `strategy_suggestions`，UI 上显示「今日建议」清单，用户可一键「应用到虚拟账本」

子决策（沿用本 ticket 决策）：
- 现金：虚拟账本 `initial_cash` 默认 10 万，可 UI 改
- 多笔买入：平均成本法（weighted avg cost）
- 手续费：默认 0.0003（万三），可配

---

## Question

系统内的"持仓"怎么建模？三种可能：

1. **全局单账户**：只有一个真实持仓视图，所有策略共用同一份"我有多少钱、持什么"
   - 优点：贴近现实
   - 缺点：换策略时难以对比（A 策略的"现在"和 B 策略的"如果当时用你"）
2. **每策略独立虚拟账户**：每个策略维护自己的虚拟持仓，PnL 各自计算
   - 优点：策略间可比
   - 缺点：和用户真实持仓脱节
3. **双轨**：真实持仓 + 每个策略的虚拟账本
   - 优点：兼容两种视角
   - 缺点：实现成本

子问题：
- 持仓表 schema：symbol、qty、cost_price、open_date、strategy_ref（FK）
- "现金"怎么表示：固定起始金额？用户手动充值/提现？
- 多笔买入合并 vs 单独记录（影响成本计算）

期望：domain-modeling 把 Position / Trade / Cash 三类对象锁死。
