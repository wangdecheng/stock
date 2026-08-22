# 09 — BaoStockBackend 实现

**What to build:**
新增 `BaoStockBackend`,实现 `Backend` 协议里 `get_bars`(日线 + 分钟线),归一化到与 AKShare 完全一致的英文列 schema(`date / open / high / low / close / volume / amount`)。`get_fundamentals` / `get_calendar` 在本 ticket 内**抛 `NotImplementedError`**(BaoStock 不覆盖这两个,后续 ticket 由路由 + fallback 决定是否走到这里)。`ping()` 返回连通性 + 延迟。

**Blocked by:** 08-extract-backend-protocol-and-adapter-facade

**Status:** ready-for-agent

- [ ] 新增 `requirements.txt` 条目 `baostock>=0.1.0`(注:实际版本号在实现时核实,只需 ≥0.1.0 的稳定版)
- [ ] `BaoStockBackend` 构造函数接受 `cache_dir`,延迟初始化 baostock 客户端(首次调用才 import)
- [ ] `get_bars(symbol, start, end, adj, frequency)` 对 daily 调用 `bs.query_history_k_data_plus` + 归一化;minute 走 baostock 的分钟接口(若有;若仅有 5/15/30/60 几个固定档位,文档说明覆盖范围并只支持存在的)
- [ ] 复权处理:BaoStock 默认就是后复权,需要确认与 AKShare `qfq` 是否等价;不等价则在归一化层做补偿,本 ticket 内至少给出**对比测试**显示差异
- [ ] `get_fundamentals` / `get_calendar` 抛 `NotImplementedError`,带明确消息("BaoStock 不覆盖此方法")
- [ ] `ping()` 用 `bs.query_all_stock` 的 count 或者一个轻量 call 测试连通性,返回 `(ok: bool, latency_ms: int, error: str | None)`
- [ ] 所有 BaoStock 调用走独立的限频器(具体阈值在 ticket 里定;baostock 文档调研,假设 ≥50 req/min 比 AKShare 宽松,精确数值写进 ticket)
- [ ] 测试:cols 归一化、空响应抛 `EmptyBarsError`、无效 symbol 抛 `UnknownSymbolError`、限频阻塞不抛(而是阻塞)
- [ ] `pytest tests/test_data_layer.py` 全绿(包含 09 新增的 baostock 测试,但不破坏 08 留下的所有 akshare 测试)