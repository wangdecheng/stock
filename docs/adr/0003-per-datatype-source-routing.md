# 按数据类型路由数据源,不支持符号级路由

不同 backend 覆盖范围不对称:BaoStock 有 A 股日/分钟 K 线但**没有** PE/PB/股息率;AKShare 全有但偶发断连。最自然的搭配是 "bars 走 BaoStock 拿稳定,fundamentals 走 AKShare 拿覆盖" —— 这几乎不会反悔,是值得记的决策。

因此配置文件按数据类型分桶:`data_source.bars.backend` / `data_source.fundamentals.backend` / `data_source.calendar.backend`。全局 `data_source.default` 作为缺省兜底。

符号级路由(`source.000001 = ...`)**暂不支持**。BaoStock 覆盖 A 股全市场,目前没有证据表明任何 symbol 需要特殊对待。YAGNI —— 真出现缺数据的票再加。

## Considered Options

- **全局一把刀**(`data_source = "akshare"`):bars 和 fundamentals 共用。问题:用户被卡在"想要稳定 bars 又想要 PE 数据"的两难里,且 PE 路由会随着回测库变化频繁改动。
- **符号级路由**:开销(配置文件膨胀、UI 表单爆炸)目前没有实际收益背书。