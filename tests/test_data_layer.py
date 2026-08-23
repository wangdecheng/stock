"""tests/test_data_layer.py

T1 acceptance tests for the data layer (CAP-1):
  * Rate-limit invariant (≤20 calls in any capacity-bound sliding window)
  * 100-call burst smoke
  * Parquet cache write-through + fallback on AKShare exception
  * EmptyBarsError / DataAdapterUnavailable / UnknownSymbolError semantics
  * `akshare` is not imported outside `framework/data/adapter.py` (CI grep)

The burst test uses a monkeypatched fast bucket so the suite runs in seconds,
not minutes. The 20-per-window invariant is what we're verifying; the window
length scales with the refill rate.
"""

from __future__ import annotations

import ast
import pathlib
import time
from datetime import date, timedelta

import pandas as pd
import pytest

from framework.data.adapter import (
    AKShareAdapter,
    DataAdapterUnavailable,
    EmptyBarsError,
    _fetch_bars_tx,
    _tencent_symbol,
)
from framework.data.cache import (
    cache_file_age_seconds,
    cache_path,
    read_cache,
    write_cache,
)
from framework.data.ratelimit import (
    GLOBAL_BUCKET,
    GLOBAL_LIMITER,
    SlidingWindowLimiter,
    TokenBucket,
    ratelimit,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_bars_df(n: int = 5, base: date = date(2024, 1, 1)) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [base + timedelta(days=i) for i in range(n)],
            "open": [10.0 + i * 0.1 for i in range(n)],
            "close": [10.1 + i * 0.1 for i in range(n)],
            "high": [10.2 + i * 0.1 for i in range(n)],
            "low": [9.9 + i * 0.1 for i in range(n)],
            "volume": [1000 * (i + 1) for i in range(n)],
            "amount": [10_000.0 * (i + 1) for i in range(n)],
        }
    )


@pytest.fixture
def adapter_with_cache(tmp_path):
    return AKShareAdapter(cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucket:
    def test_starts_full(self):
        b = TokenBucket(capacity=20, refill_per_second=20 / 60)
        assert b.available == pytest.approx(20.0, abs=0.01)

    def test_acquire_drains_then_blocks(self):
        b = TokenBucket(capacity=5, refill_per_second=10)  # 0.1s per token
        for _ in range(5):
            assert b.acquire(timeout=0.5)
        # 6th call should block ~0.1s before succeeding
        start = time.monotonic()
        assert b.acquire(timeout=0.5)
        elapsed = time.monotonic() - start
        assert 0.05 <= elapsed <= 0.3

    def test_timeout_returns_false(self):
        # 10s-per-token refill, ask for 1 token with 0.2s timeout
        b = TokenBucket(capacity=5, refill_per_second=0.1)
        for _ in range(5):
            b.acquire(timeout=0.1)
        assert not b.acquire(tokens=1, timeout=0.2)

    def test_over_capacity_raises(self):
        b = TokenBucket(capacity=5, refill_per_second=1)
        with pytest.raises(ValueError, match="exceed bucket capacity"):
            b.acquire(tokens=6)


# ---------------------------------------------------------------------------
# ratelimit decorator + burst test
# ---------------------------------------------------------------------------


class TestRatelimitDecorator:
    def test_passes_through_args(self):
        @ratelimit
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_works_on_methods(self):
        class C:
            @ratelimit
            def m(self, x):
                return x * 2

        assert C().m(5) == 10

    def test_global_limiter_is_production_config(self):
        # CAP-1 constraint: 20 req/min/IP, strict "≤20 in any 60s sliding window"
        assert GLOBAL_LIMITER._max_calls == 20
        assert GLOBAL_LIMITER._window == pytest.approx(60.0, rel=1e-3)
        # Backwards-compat alias still resolves
        assert GLOBAL_BUCKET is GLOBAL_LIMITER

    def test_burst_100_calls_bounded_sliding_window(self, monkeypatch):
        """100-call loop: any window of `window_seconds` contains ≤ `max_calls`
        actual invocations.

        Uses a fast limiter (20 calls / 1.0s — same ratio as 20 / 60s) so the
        test runs in ~5 s. The invariant is independent of the window length.
        """
        fast = SlidingWindowLimiter(max_calls=20, window_seconds=1.0)
        monkeypatch.setattr(
            "framework.data.ratelimit.GLOBAL_LIMITER", fast, raising=True
        )

        timestamps: list[float] = []

        @ratelimit
        def stub():
            timestamps.append(time.monotonic())

        start = time.monotonic()
        for _ in range(100):
            stub()
        elapsed = time.monotonic() - start
        assert elapsed < 10.0, f"100 calls took {elapsed:.1f}s (expected <10s)"

        # Sliding-window check: in any 1.0s window, ≤ 20 actual calls landed.
        timestamps.sort()
        max_in_window = 0
        for i, t in enumerate(timestamps):
            window_end = t + 1.0
            count = sum(1 for u in timestamps[i:] if u <= window_end)
            max_in_window = max(max_in_window, count)
        assert max_in_window <= 20, (
            f"expected ≤20 in any 1s sliding window, got {max_in_window}"
        )

        # Burst sanity: at least 10 calls landed within the first 100 ms —
        # the limiter allows the full quota to land up front.
        first_100ms = sum(1 for t in timestamps if t <= timestamps[0] + 0.1)
        assert first_100ms >= 10, f"expected fast burst, only {first_100ms} in 100ms"


# ---------------------------------------------------------------------------
# Parquet cache
# ---------------------------------------------------------------------------


class TestCache:
    def test_write_read_roundtrip(self, tmp_path):
        df = _make_bars_df()
        path = write_cache(tmp_path, "000001", "daily", "qfq", df)
        assert path.exists()

        loaded = read_cache(tmp_path, "000001", "daily", "qfq")
        assert loaded is not None
        pd.testing.assert_frame_equal(loaded, df, check_dtype=False)

    def test_read_returns_none_when_missing(self, tmp_path):
        assert read_cache(tmp_path, "999999", "daily", "qfq") is None

    def test_path_layout(self, tmp_path):
        p = cache_path(tmp_path, "000001", "daily", "qfq")
        assert p == tmp_path / "000001" / "daily_qfq.parquet"

    def test_age_seconds_is_small_after_fresh_write(self, tmp_path):
        df = _make_bars_df()
        path = write_cache(tmp_path, "000001", "daily", "qfq", df)
        assert cache_file_age_seconds(path) < 5


# ---------------------------------------------------------------------------
# DataAdapter behavior
# ---------------------------------------------------------------------------


class TestAKShareAdapter:
    def test_get_bars_writes_through_on_success(
        self, adapter_with_cache, monkeypatch, tmp_path
    ):
        df = _make_bars_df()
        monkeypatch.setattr(adapter_with_cache, "_fetch_bars", lambda *a, **kw: df)

        result = adapter_with_cache.get_bars(
            "000001", date(2024, 1, 1), date(2024, 1, 5)
        )
        assert result.cache_hit is False
        assert result.stale_seconds == 0
        # Cache file should now exist
        assert (tmp_path / "000001" / "daily_qfq.parquet").exists()

    def test_get_bars_falls_back_to_cache_on_akshare_exception(
        self, adapter_with_cache, monkeypatch, tmp_path
    ):
        cached = _make_bars_df()
        write_cache(tmp_path, "000001", "daily", "qfq", cached)

        def fail(*a, **kw):
            raise ConnectionError("akshare unreachable")

        monkeypatch.setattr(adapter_with_cache, "_fetch_bars", fail)

        result = adapter_with_cache.get_bars(
            "000001", date(2024, 1, 1), date(2024, 1, 5)
        )
        assert result.cache_hit is True
        assert result.stale_seconds >= 0
        pd.testing.assert_frame_equal(result.df, cached, check_dtype=False)

    def test_get_bars_raises_emptybars_on_empty_upstream_not_cache(
        self, adapter_with_cache, monkeypatch, tmp_path
    ):
        # Even with cache present, empty upstream must raise (not silently cache)
        write_cache(tmp_path, "000001", "daily", "qfq", _make_bars_df())
        monkeypatch.setattr(
            adapter_with_cache, "_fetch_bars", lambda *a, **kw: None
        )

        with pytest.raises(EmptyBarsError):
            adapter_with_cache.get_bars(
                "000001", date(2024, 1, 1), date(2024, 1, 5)
            )

    def test_get_bars_raises_emptybars_on_empty_dataframe(
        self, adapter_with_cache, monkeypatch
    ):
        monkeypatch.setattr(
            adapter_with_cache, "_fetch_bars", lambda *a, **kw: pd.DataFrame()
        )
        with pytest.raises(EmptyBarsError):
            adapter_with_cache.get_bars(
                "000001", date(2024, 1, 1), date(2024, 1, 5)
            )

    def test_get_bars_raises_unavailable_when_no_cache_no_upstream(
        self, adapter_with_cache, monkeypatch
    ):
        def fail(*a, **kw):
            raise ConnectionError("akshare unreachable")

        monkeypatch.setattr(adapter_with_cache, "_fetch_bars", fail)
        with pytest.raises(DataAdapterUnavailable):
            adapter_with_cache.get_bars(
                "999999", date(2024, 1, 1), date(2024, 1, 5)
            )


# ---------------------------------------------------------------------------
# Tencent fallback helpers
# ---------------------------------------------------------------------------


class TestTencentSymbolPrefix:
    @pytest.mark.parametrize(
        "raw,prefixed",
        [
            ("000001", "sz000001"),   # Shenzhen main
            ("002415", "sz002415"),   # Shenzhen SME
            ("300750", "sz300750"),   # ChiNext
            ("200001", "sz200001"),   # Shenzhen B-share
            ("159992", "sz159992"),   # Shenzhen ETF
            ("600519", "sh600519"),   # Shanghai main
            ("688981", "sh688981"),   # STAR
            ("510500", "sh510500"),   # Shanghai ETF
            ("900901", "sh900901"),   # Shanghai B-share
        ],
    )
    def test_adds_market_prefix(self, raw, prefixed):
        assert _tencent_symbol(raw) == prefixed

    @pytest.mark.parametrize("raw", ["sz000001", "sh600519", "SH600519", "sz159992"])
    def test_idempotent_when_already_prefixed(self, raw):
        out = _tencent_symbol(raw)
        assert out == raw.lower()


class TestFetchBarsTx:
    """Pure-function tests for the Tencent daily fetcher.

    `_fetch_bars_tx` reaches the network. Tests stub `ak.stock_zh_a_hist_tx`
    so they don't hit the upstream; they exercise the column mapping, the
    volume approximation, and the empty-response path.
    """

    def _stub_akshare_tx(self, monkeypatch, df):
        """Patch `ak.stock_zh_a_hist_tx` at the akshare module level so it
        works regardless of where the adapter imports `ak`."""
        def _fake(symbol, start_date, end_date, adjust, timeout=None):
            # The adapter passes timeout=10.0 for hang protection. Tests don't
            # need to act on it; just accept the kwarg so the fake matches
            # the real AKShare signature (akshare 1.18.x+).
            assert adjust == "qfq"
            assert symbol.startswith(("sh", "sz"))
            return df

        import akshare
        monkeypatch.setattr(akshare, "stock_zh_a_hist_tx", _fake, raising=False)

    def test_normalizes_columns_and_approximates_volume(self, monkeypatch):
        df = pd.DataFrame(
            {
                "date": ["2025-08-20", "2025-08-21"],
                "open": ["11.20", "11.36"],
                "close": ["11.40", "11.41"],
                "high": ["11.40", "11.46"],
                "low": ["11.19", "11.32"],
                "amount": ["1183578.0", "869128.0"],
            }
        )
        self._stub_akshare_tx(monkeypatch, df)

        out = _fetch_bars_tx(
            "000001", date(2025, 8, 20), date(2025, 8, 21), adj="qfq"
        )
        assert list(out.columns) == [
            "date", "open", "high", "low", "close", "volume", "amount"
        ]
        # volume (手) = amount (元) / (close × 100)
        # 1183578 / (11.40 * 100) ≈ 1038.23
        first = out.iloc[0]
        assert first["close"] == 11.40
        assert first["amount"] == 1183578.0
        assert first["volume"] == pytest.approx(1183578.0 / (11.40 * 100))

    def test_returns_none_for_empty_upstream(self, monkeypatch):
        self._stub_akshare_tx(monkeypatch, None)
        assert _fetch_bars_tx(
            "000001", date(2025, 8, 20), date(2025, 8, 21), adj="qfq"
        ) is None

        self._stub_akshare_tx(monkeypatch, pd.DataFrame())
        assert _fetch_bars_tx(
            "000001", date(2025, 8, 20), date(2025, 8, 21), adj="qfq"
        ).empty

    def test_volume_approximation_handles_zero_close(self, monkeypatch):
        # Defensive: if close=0 (e.g. suspended bar), volume is NA, not inf.
        df = pd.DataFrame(
            {
                "date": ["2025-08-20"],
                "open": ["0.0"],
                "close": ["0.0"],
                "high": ["0.0"],
                "low": ["0.0"],
                "amount": ["0.0"],
            }
        )
        self._stub_akshare_tx(monkeypatch, df)

        out = _fetch_bars_tx(
            "000001", date(2025, 8, 20), date(2025, 8, 20), adj="qfq"
        )
        assert pd.isna(out.iloc[0]["volume"])


# ---------------------------------------------------------------------------
# Eastmoney → Tencent fallback in `_fetch_bars`
# ---------------------------------------------------------------------------


class TestEastmoneyToTencentFallback:
    """Verify `_fetch_bars` falls back to Tencent when eastmoney raises."""

    def test_eastmoney_success_skips_tencent(
        self, adapter_with_cache, monkeypatch
    ):
        # Stub eastmoney (ak.stock_zh_a_hist) to return a valid DataFrame.
        # The fallback path must NOT call Tencent on success.
        em_df = _make_bars_df()
        tencent_calls: list[tuple] = []

        import akshare

        def _fake_eastmoney(*a, **kw):
            return em_df

        monkeypatch.setattr(akshare, "stock_zh_a_hist", _fake_eastmoney, raising=False)

        def _spy_tencent(*a, **kw):
            tencent_calls.append((a, kw))
            raise AssertionError("Tencent must not be called when eastmoney succeeds")

        monkeypatch.setattr(
            "framework.data.adapter._fetch_bars_tx", _spy_tencent, raising=True
        )

        result = adapter_with_cache.get_bars(
            "000001", date(2024, 1, 1), date(2024, 1, 5)
        )
        assert result.cache_hit is False
        assert result.stale_seconds == 0
        # Compare on sorted columns to avoid order sensitivity; eastmoney
        # normalization may reorder after the AKShare rename.
        pd.testing.assert_frame_equal(
            result.df[sorted(result.df.columns)],
            em_df[sorted(em_df.columns)],
            check_dtype=False,
        )
        assert tencent_calls == []

    def test_eastmoney_exception_triggers_tencent_fallback(
        self, adapter_with_cache, monkeypatch, tmp_path
    ):
        tx_df = _make_bars_df(base=date(2024, 1, 1))
        monkeypatch.setattr(
            "framework.data.adapter._fetch_bars_tx",
            lambda *a, **kw: tx_df,
            raising=True,
        )

        import akshare

        def _fake_eastmoney(symbol, period, start_date, end_date, adjust, timeout=None):
            raise ConnectionError("eastmoney WAF-blocked")

        monkeypatch.setattr(akshare, "stock_zh_a_hist", _fake_eastmoney, raising=False)

        result = adapter_with_cache.get_bars(
            "000001", date(2024, 1, 1), date(2024, 1, 5)
        )
        assert result.cache_hit is False
        assert result.stale_seconds == 0
        pd.testing.assert_frame_equal(result.df, tx_df, check_dtype=False)
        # Cache write-through happened on the fallback path
        assert (tmp_path / "000001" / "daily_qfq.parquet").exists()

    def test_both_upstreams_fail_propagates_eastmoney_error(
        self, adapter_with_cache, monkeypatch
    ):
        monkeypatch.setattr(
            "framework.data.adapter._fetch_bars_tx",
            lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("tencent blocked")),
            raising=True,
        )

        import akshare

        def _fake_eastmoney(symbol, period, start_date, end_date, adjust, timeout=None):
            raise ConnectionError("eastmoney WAF-blocked")

        monkeypatch.setattr(akshare, "stock_zh_a_hist", _fake_eastmoney, raising=False)

        # No cache → DataAdapterUnavailable wraps the eastmoney error
        with pytest.raises(DataAdapterUnavailable) as ei:
            adapter_with_cache.get_bars(
                "999999", date(2024, 1, 1), date(2024, 1, 5)
            )
        assert "eastmoney WAF-blocked" in str(ei.value)

    def test_minute_frequency_does_not_fall_back(self, adapter_with_cache, monkeypatch):
        # If eastmoney fails on minute bars, Tencent must NOT be attempted
        # (Tencent has no minute endpoint).
        import akshare

        def _fake_min_em(*a, **kw):
            raise ConnectionError("eastmoney minute blocked")

        monkeypatch.setattr(
            akshare, "stock_zh_a_hist_min_em", _fake_min_em, raising=False
        )

        monkeypatch.setattr(
            "framework.data.adapter._fetch_bars_tx",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("Tencent must not be called for minute frequency")
            ),
            raising=True,
        )

        with pytest.raises(DataAdapterUnavailable):
            adapter_with_cache.get_bars(
                "000001", date(2024, 1, 1), date(2024, 1, 5), frequency="60m"
            )


# ---------------------------------------------------------------------------
# CI-style grep: akshare isolation
# ---------------------------------------------------------------------------


def test_akshare_only_imported_in_adapter_py():
    """`framework/data/adapter.py` is the only production file that may
    import akshare. The data-layer test file is allowed to import akshare
    because it patches the integration boundary; production code outside
    `framework/data/adapter.py` must not bypass the adapter.

    AST-level check only (string-level would catch the literal text used by
    this very test). Catches `import akshare`, `from akshare import ...`,
    and `from akshare.foo import ...`.
    """
    roots = ["framework", "app.py", "pages", "strategies", "tests"]
    allowed = {"framework/data/adapter.py", "tests/test_data_layer.py"}
    violations: list[str] = []

    for root in roots:
        rp = pathlib.Path(root)
        if not rp.exists():
            continue
        files = [rp] if rp.is_file() else list(rp.rglob("*.py"))
        for p in files:
            rel = str(p)
            if rel in allowed:
                continue
            try:
                tree = ast.parse(p.read_text(), filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        if n.name.split(".")[0] == "akshare":
                            violations.append(f"{rel}:{node.lineno} (import {n.name})")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0] == "akshare":
                        violations.append(f"{rel}:{node.lineno} (from {node.module})")

    assert not violations, (
        "akshare must only be imported in adapter.py, found in:\n  "
        + "\n  ".join(violations)
    )