# 11 — 显式 fallback 链 + BarsResult.source 字段

**What to build:**
`SourceRoute(primary, fallback?)` 在 config 里声明主备,`DataAdapter.get_bars` 内部按"primary → fallback → cache"顺序处理。`EmptyBarsError` / `UnknownSymbolError` / 限频阻塞**不触发** fallback(尊重 backend 结论、避免无谓请求、限频应等而不是换家)。`BarsResult` 新增 `source: str` 字段标明数据实际来自哪家。

**Blocked by:** 10-config-loader-and-per-datatype-routing

**Status:** ready-for-agent

- [ ] `config.toml` schema 扩展为数组形式:`[[data_source.bars]] backend = "akshare"; fallback = "baostock"`(向后兼容 ticket 10 的单值写法,自动转为 `fallback = None`)
- [ ] `SourceRoute` dataclass:`primary: str`、`fallback: str | None`
- [ ] `DataAdapter.get_bars` 编排顺序:
 1. 取当前 datatype 的 `SourceRoute`
 2. 调 primary;成功 → 写缓存 → 返回 `BarsResult(source=primary, ...)`
 3. primary 抛 `DataBackendUnavailable`(网络/DNS/解析)→ 试 fallback(若有);成功 → 写缓存 → 返回 `BarsResult(source=fallback, ...)`
 4. fallback 也抛或不存在 → 读缓存;命中 → 返回 `BarsResult(source=primary, stale_seconds=..., cache_hit=True)`(注意 cache 标 primary,因为缓存是 primary 写入的)
 5. 缓存也不在 → 抛 `DataAdapterUnavailable`
- [ ] `EmptyBarsError` / `UnknownSymbolError` **直接抛出**,不读 fallback、不读缓存
- [ ] 新增 `DataBackendUnavailable` 异常类(单 backend 失败,adapter 内部用它判定是否走 fallback);`UnknownSymbolError` 消息改为"被任何 backend 拒绝"
- [ ] 限频阻塞:每个 backend 维护独立的 `SlidingWindowLimiter`,调用前 acquire,timeout 返回 `RateLimitTimeout`(新异常);**不**走 fallback
- [ ] `ratelimit` 装饰器改造为 `ratelimit_for(backend_name)` 形式,从全局 limiter 注册表取对应 limiter
- [ ] `BarsResult` 新增 `source: str` 字段,默认 `"akshare"`(向后兼容老调用方)
- [ ] 测试覆盖矩阵:primary 成功 / primary 网络挂 + fallback 成功 / primary 网络挂 + fallback 也挂 + cache 命中 / primary 网络挂 + 无 fallback + cache 缺失 / primary empty(不触发 fallback)/ primary unknown(不触发 fallback)
- [ ] 所有现有测试不破坏(ticket 10 的调用方会自动适配 `BarsResult.source` 默认值)