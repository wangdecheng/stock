# 共享 parquet 缓存 + sidecar `.meta.json` 元数据

缓存是**最后写入者赢**。如果按 source 分仓(`data/cache/akshare/...` + `data/cache/baostock/...`),切源时**已缓存的票被强制冷启动**,白白吃掉限频配额。而两家在历史 bar 上几乎一致(都源自交易所公开数据),分仓是**假性隔离**。

因此采用**单一 parquet** 仓,加 sidecar `.meta.json` 记录来源:

```
data/cache/600519/daily_qfq.parquet
data/cache/600519/daily_qfq.parquet.meta.json   ← {"source": "baostock", "fetched_at": "..."}
```

sidecar 是旁路 —— 现有 `read_cache` 调用零修改,只在 debug / `BarsResult.source` 追溯时读它。

迁移:存量 `600519/daily_qfq.parquet` 没有 sidecar,读时视作 legacy AKShare 数据(写它的时候还没多源)。

## Considered Options

- **按 source 分仓**:切换源 = 缓存冷启动,吃限频配额;调试时还得记得切到哪个目录。
- **共享 + 把 source 塞进 parquet schema**:更"自包含",但读热路径要改,且把领域元数据塞进分析文件本身有点脏。