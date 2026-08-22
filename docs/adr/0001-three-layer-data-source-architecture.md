# 三层数据源架构:DataSource / DataBackend / DataAdapter

多源切换之前,`AKShareAdapter` 一个类同时承担"名号 / 实现 / 对外门面"三种角色,只有一家时无伤大雅。一旦 ≥2 家,这三层生命周期完全不同 —— 名号写在配置文件里、实现由 factory 构造、门面是单例 —— 必须拆开,否则后续每加一个数据源都会撞一次命名歧义。

因此定义 `DataSource`(纯名号,配置文件值) / `DataBackend`(实现,持有该上游的限频器和连接) / `DataAdapter`(单例门面,编排 backend + 缓存 + fallback)。原有 `AKShareAdapter` 重命名为 `DataAdapter`,新增 `AKShareBackend` / `BaoStockBackend`。`get_bars` 等公共方法签名保持不变,调用方零改动。