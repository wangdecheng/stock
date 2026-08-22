# A 股量化 · 域模型

数据获取与策略执行的领域词汇表。本文件只列**本项目特有**的术语;通用编程概念(限频算法、IO 错误类型等)不在此。

## Language

### 数据获取层

**DataSource**:
数据上游的**身份标识**。配置文件中以字符串出现(`akshare` / `baostock`)。本身不携带方法或状态,只是名号。
_Avoid_: 数据源(这个名字留作整层概念的统称)、provider、vendor

**DataBackend**:
实现了某个具体 DataSource 的 Python 对象。负责与该上游通信并把响应归一化到统一 schema。每个 backend 自带独立的限频器。
_Avoid_: source(歧义)、client、driver

**DataAdapter**:
整个 app 的**单例门面**。持有当前 backend 映射 + 缓存层 + 限频器 + fallback 链路编排。对 UI、strategy、runner 暴露 `get_bars` / `get_fundamentals` / `get_calendar`。调用方感知不到 backend 的存在。
_Avoid_: API、service、gateway

**Primary Source**:
某条数据请求**默认应走**的 backend,由配置文件中的 `data_source.<datatype>.backend` 指定。
_Avoid_: default source、main source

**Fallback Source**:
配置中显式声明的**第二选择**,仅在 primary 抛**不可恢复异常**时启用。配置里没写就不存在。
_Avoid_: backup、secondary、alternate

### 路由与回退

**Source Route**:
配置文件中**按数据类型**(`bars` / `fundamentals` / `calendar`)选择 backend 的能力。符号级路由(symbol 级指定 backend)**目前不支持**。
_Avoid_: source map、binding

**Fallback Chain**:
配置中显式声明的"主→备"序列,**至多二级**。隐式自动回退不在本项目存在 —— 任何回退都必须在配置里被命名。
_Avoid_: failover chain(隐含自动触发)、retry chain(暗示重复尝试)

### 数据结果

**BarsResult.stale_seconds**:
本次结果距缓存写入的秒数。`= 0` 表示来自上游实时拉取;`> 0` 表示走了缓存兜底(无论 primary 还是 fallback)。UI 据此渲染"数据延迟"徽标。
_Avoid_: stale(无单位含义)、age

**BarsResult.cache_hit**:
本次结果是否读自 parquet 缓存(无论 source)。与 `stale_seconds` 正交 —— cache_hit=True 时 stale_seconds 必定 > 0。
_Avoid_: from_cache(口语)

**BarsResult.source**:
实际产出本次数据的 backend 名(`akshare` / `baostock`)。允许 UI 诚实显示"数据来自 baostock 兜底"。
_Avoid_: origin、producer

### 缓存

**Cache Parquet**:
`(symbol, freq, adj)` 三元组唯一对应的 parquet 文件,路径 `data/cache/<symbol>/<freq>_<adj>.parquet`。所有 DataSource 共用同一份。
_Avoid_: source file、bar file

**Cache Sidecar**:
每个 cache parquet 旁的 `<...>.meta.json`,记录 `{"source": "...", "fetched_at": "..."}`。无 sidecar 的存量文件视为 legacy AKShare 数据。
_Avoid_: metadata(无范围)、manifest

### 错误类型

**EmptyBarsError**:
backend **识别了 symbol 但返回空数据**。**不**走 fallback、不读缓存 —— 因为空响应意味着 backend 在该 symbol 上有结论。
_Avoid_: not found(语义不同)、empty result

**DataAdapterUnavailable**:
所有声明的 backend(含 fallback)都失败,且缓存不可用。这是**最终**错误。
_Avoid_: service down(语义太宽)

**UnknownSymbolError**:
任何 backend 都无法识别该 symbol。**不**走 fallback、不读缓存。
_Avoid_: invalid symbol、bad symbol

### 策略层

**StateMachine**:
本策略的 **4-regime 分类器**(TREND_UP / TREND_DOWN / RANGE_BULL / RANGE_BEAR)。按当日 ADX、+DI/-DI、MA20/MA60 的条件把市场归到 4 个状态之一。**不是**通用 FSM —— 状态集、转移条件、默认权重都是本策略固定的。
_Avoid_: FSM、state machine(太泛)、classifier、regime model

**TrendState**:
StateMachine 的**枚举输出域**:`{TREND_UP, TREND_DOWN, RANGE_BULL, RANGE_BEAR}`。每根 bar 输出**唯一**一个值;持久化键 `current_state` 与 `pending_state` 都用这个枚举的字符串。
_Avoid_: regime、state(歧义)、market state、regime enum

**Hysteresis Gate**:
进入 TREND_UP 前的**确认门槛**:ADX > 25 且 ADX 连续 2 日上升 且 Close > BB mid,三者**连续 2 个 bar** 同时成立才放行。专门过滤单日 ADX 抖动引发的假突破。持久化键 `pending_state` + `pending_days` 用来计数。
_Avoid_: confirmation、debounce、entry filter、ADX filter

**Phased Exit**:
离开任一持仓状态时,目标权重走 **N 日线性**(默认 2 日)到新 default。第 1 天走一半、第 2 天走完。比瞬时全卖降低"踩在反转日谷底"的风险。持久化键 `exit_in_progress = {target, days_left}`。
_Avoid_: gradual exit、ladder exit、scaled exit、drip exit

**HalvedStage**:
TREND_UP **内部**的三段子状态:`full → halved → cleared`。`full` 时 Close < MA20 → `halved`(权重 0.50);`halved` 时 Close < MA10 → `cleared`(权重 0.00)。允许趋势中段一次 MA20 抖动不直接清仓。持久化键 `trend_up_stage`。
_Avoid_: stop stage、position stage、trailing stage、internal state

**Signal Modulator**:
RANGE_BULL 买入信号叠加的 **0–1 连续标量**:`weight = 0.50 + 0.5 * clamp((30 - RSI) / 10, 0, 1)`。RSI 越极端(< 20 → 1.0, ≥ 30 → 0.0)加仓越多;**不阻塞**弱信号(只增不减)。
_Avoid_: RSI multiplier、position scaler、weight adjuster、signal weight

**Adaptive Trigger**:
RANGE_BULL 的**多源买入触发器**,三选一即触发:BB 下轨触碰 / 长下影线(`(Close - Low) > 2 * |Close - Open|`) / 看涨吞没。卖出触发**只看** BB 上轨触碰(单一源)。比单指标更稳定。
_Avoid_: entry signal、buy trigger(歧义)、composite signal、multi-signal