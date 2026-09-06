# 股票详情页指标面板选 B 方案(K 线 + ADX + RSI,MA10 故意不画)

股票详情页(`pages/1_股票详情.py`)要把 ADX+BB 策略用到的所有指标画出来。原 D5 T10 决定是「K 线 + 成交量」2 栏,本轮新增策略对齐需求后调整为:

```
Panel 0  主图    K 线 + MA20 + MA60 + BB 三轨 + 触线 MarkPoint + TrendState 4 色带
Panel 1  ADX     +DI / -DI / ADX 三线 + 25/20 阈值参考线 + HalvedStage 3 色带
Panel 2  RSI     RSI(14) 单线 + 30 阈值参考线
```

`build_volume` 不再被该页调用,详情见 ADR-0008。

## Considered Options

- **方案 A 极简**:主图只叠 MA10/20/60,没有 ADX / RSI / 状态色带。优点:主图最干净;缺点:StateMachine 的 `_classify` 用了 6 个原始信号(ADX、+DI、-DI、MA20、MA60、BB mid),A 方案在图上只能看到 4 个,**D3 cascade 的 ADX 25 / ADX 20 阈值不可见**,策略逻辑跟图再次脱节。
- **方案 B(本方案)**:`MA10 故意不画`+ `MA20/MA60 留在主图`+ `BB 通道 + 触线 MarkPoint`+ `ADX / RSI 独立副图`+ `两条状态色带`。MA10 是 T04 第二段止损(`halved → cleared`)专用,只在主图会让 K 线被三条均线 + 通道 + 色带切碎;HalvedStage 移到 ADX 副图下方的窄色带更贴近语境。
- **方案 C 完整**:MA10/20/60 全画在主图。K 线被 4 条线切碎;触线 MarkPoint 跟均线会视觉打架;用户实际关心的「今天是不是 TREND_UP」反而被淹没。
- **方案 D 自定**:放到 grill 那轮让用户列,用户回了「按推荐」就回退到 B。

## Decision

采用 **方案 B**。

理由:

1. **图-策略对齐的最小集**:D3 cascade + T04 HalvedStage + T05 Phased Exit + T06 Adaptive Trigger,图上**至少**要看到 6 个原始信号(ADX、+DI、-DI、MA20、MA60、BB 三轨)外加 2 个状态色带。B 是覆盖这些的最小 panel 数。
2. **MA10 的取舍**:T04 把 MA10 用作「halved → cleared」的二次触发,而不是分级信号。把它留在主图会挤压 K 线可读性,挪去 ADX 副图或干脆不画都可以接受。本方案选**不画**(策略作者调试 T04 时,可以通过 ADX 副图下方的 HalvedStage 色带直接看到 `cleared` 何时发生,不需要看 MA10 本身)。
3. **RSI 单独副图**:RSI 是 T06 Signal Modulator 的输入,值域 [0, 100] 跟价格不可比,叠主图会导致双纵轴混乱(用户已经在 CONTEXT.md 反对过这种「DualAxis」反模式)。单独副图 + 30 阈值参考线足以表达「RSI 越极端,买入仓位越满」的语义。
4. **BB 通道而非只画中轨**:用户原 Q2 的语义是「Adaptive Trigger 在 RANGE_BULL 才生效」,但 BB 触线本身是 K 线相对位置的视觉锚,**主图默认就画三轨**(A 股惯例),触线 MarkPoint 只在策略真实发出信号的 bar 出现,避免噪声。

## Consequences

- `framework/charts/__init__.py` 需要新增模块级常量:`TREND_STATE_COLORS`、`HALVED_STAGE_COLORS`、`INDICATOR_LINE_COLORS`(分别管 4 + 3 + 多条指标线的颜色)。
- `build_indicator_grid(...)` 新增,作为 `pages/1_股票详情.py` 的入口;`build_kline_volume_grid` 保留但本轮不被调用。
- 副图 1(ADX)的视觉密度最高(三线 + 两参考线 + 一色带),需在测试里加 `test_adx_panel_*` 锁定关键参数(grid 高度、legend 顺序、参考线颜色)。
- 未来如果有人想加 MACD 副图,**会问「为什么不放进 ADX 副图」**,届时可援引本 ADR:每个副图限一个指标族,避免 K 线图变信号泥沼。
