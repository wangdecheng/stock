# Companion — Data Adapter

This companion is part of `SPEC-a-stock-quant` (CAP-1). It is load-bearing: any implementer of the data layer must read this.

## Single source of truth

**AKShare.** No Tushare Pro, no BaoStock, no direct east-money/new-Sina hits. (Source: T1 Resolution.)

Accepted gaps (decided in T1, documented for posterity, do **not** "fix" silently):

| Gap | Why accepted | Revive condition |
|---|---|---|
| North-bound capital (`stock_hsgt_hist_em`) dead since 2024-08-19 | Would require Tushare Pro + token | Open a new ticket if a strategy needs it |
| Adjustment-factor precision (ST / IPO / restructuring → 10%+ cumulative error) | T1 explicitly accepted | Open a new ticket if accuracy matters |

T4-research found the layer-up would actually be "AKShare (research) + Tushare 2000-credit tier (prod) + BaoStock (fallback)". T1 closed the door on that. Future tickets may re-open it but must not be silently inverted.

## `DataAdapter` interface (mandatory)

```python
class DataAdapter(Protocol):
    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adj: Literal["qfq", "hfq", "none"] = "qfq",
        frequency: Literal["daily", "1m", "5m", "15m", "60m"] = "daily",
    ) -> pd.DataFrame:  # columns: date, open, high, low, close, volume, amount
        ...

    def get_fundamentals(self, symbol: str) -> dict:
        ...

    def get_calendar(self, start: date, end: date) -> list[date]:
        ...
```

Strategies and engine only see this. No `import akshare` allowed outside `data/adapter.py`.

## AKShare interface mapping (implementation cheat sheet)

| Method | AKShare endpoint |
|---|---|
| `get_bars(daily, qfq)` | `ak.stock_zh_a_hist(symbol=..., start_date=..., end_date=..., adjust="qfq")` |
| `get_bars(daily, none)` | same with `adjust=""` |
| `get_bars(minute)` | `ak.stock_zh_a_hist_min_em(symbol=..., period="1"/"5"/"15"/"30"/"60")` |
| `get_fundamentals` | `ak.stock_financial_report_sina(stock=...)` (or q.stock from eastmoney for valuation) |
| `get_calendar` | `ak.tool_trade_date_hist_sina()` filtered to range |

Frequency enum values must exactly match AKShare's `period` strings; mapping table below:

| Enum | AKShare `period` |
|---|---|
| `1m` | `"1"` |
| `5m` | `"5"` |
| `15m` | `"15"` |
| `60m` | `"60"` |

## Rate-limit hard wall: 20 req/min/IP

Eastmoney's iron rule (T4). Exceeding it = temporary or permanent IP block.

Implementation:

1. **Global token bucket**: 20 tokens, refill 20 per 60 s, blocking on empty.
2. **Per-method `ratelimit` decorator** wrapping every public DataAdapter method (defense in depth; in-process call sites cannot bypass the bucket).
3. **Burst tests** (CAP-1 success): a 100-call loop against a stub endpoint must show ≤ 20 calls land in any 60 s sliding window.

## Cache fallback ("数据延迟" path)

Storage: local parquet under `data/cache/<symbol>/<freq>_<adj>.parquet`.

- Every successful `get_bars` call writes through to the cache.
- On `akshare` exception (network, DNS, HTTP error), the adapter returns the most recent parquet overlapping the requested window, plus a `CacheHit` flag the UI reads to render a "数据延迟" badge.
- Cache is advisory (may be missing or older than requested). It must not be used as a primary source when AKShare is healthy.
- Cache directory is gitignored.

## Failure modes the adapter must handle

| Failure | Behavior |
|---|---|
| AKShare returns empty DataFrame | Raise `EmptyBarsError`; do **not** return cached data silently |
| AKShare raises any exception | Log + try cache; raise `DataAdapterUnavailable` only if cache is also missing |
| Symbol unknown to AKShare | Raise `UnknownSymbolError` |
| Rate-limit would be exceeded | Block (sync) until token available — never silently drop |
| Requested window > cache freshness | Cache is returned with `stale_seconds=N` populated in the result wrapper |

## Out-of-scope for `DataAdapter` (explicit)

- Pre-aggregation / factor precomputation. Strategies call `ctx.bars(symbol, lookback)` and the framework may add value columns on demand.
- Authentication tokens for AKShare. None today.
- Multi-source failover (Tushare / BaoStock). Single-source by design.
