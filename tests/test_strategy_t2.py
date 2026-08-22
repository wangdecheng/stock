"""tests/test_strategy_t2.py

Contract tests for T2 (Strategy base + Context + discover + params/state
persistence). These pin the behavior described in
`specs/spec-a-stock-quant/strategy-interface.md` (CAP-3, CAP-4).

Each test exercises one seam in isolation:
  * persistence round-trip (params.json + state.json)
  * discover (file scan + decoupling guard + duplicate-name handling)
  * Context data + indicator dispatch (with an injected fake adapter / registry)
"""

from __future__ import annotations

import textwrap
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from framework.indicators import register as indicators_register
from framework.strategy.context import Context, UnknownIndicatorError


# ---------------------------------------------------------------------------
# Persistence seam
# ---------------------------------------------------------------------------


def _import_persistence():
    from framework.strategy import persistence

    return persistence


def test_persistence_params_round_trip(tmp_path: Path):
    persistence = _import_persistence()
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()

    payload = {
        "strategy_class": "strategies.ma_cross.MACrossStrategy",
        "strategy_name": "ma_cross",
        "params": {"short": 5, "long": 20, "drift_threshold": 0.05},
        "updated_at": "2026-08-22T10:00:00Z",
    }
    persistence.save_params(strategies_dir, "ma_cross", payload)
    loaded = persistence.load_params(strategies_dir, "ma_cross")

    assert loaded == payload
    assert (strategies_dir / "ma_cross.params.json").is_file()


def test_persistence_state_round_trip(tmp_path: Path):
    persistence = _import_persistence()
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()

    state = {"last_signal_date": "2026-08-22", "counter": 7, "items": ["a", "b"]}
    persistence.save_state(strategies_dir, "ma_cross", state)
    assert persistence.load_state(strategies_dir, "ma_cross") == state

    state["counter"] = 8
    persistence.save_state(strategies_dir, "ma_cross", state)
    assert persistence.load_state(strategies_dir, "ma_cross") == state


def test_persistence_load_params_missing_returns_none(tmp_path: Path):
    persistence = _import_persistence()
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    assert persistence.load_params(strategies_dir, "ghost") is None


def test_persistence_load_state_missing_returns_empty_dict(tmp_path: Path):
    persistence = _import_persistence()
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    assert persistence.load_state(strategies_dir, "ghost") == {}


def test_persistence_atomic_write_does_not_leave_tmp(tmp_path: Path):
    persistence = _import_persistence()
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    persistence.save_params(strategies_dir, "x", {"a": 1})
    leftovers = [p.name for p in strategies_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"stray temp files: {leftovers}"


def test_persistence_state_rejects_non_json_values(tmp_path: Path):
    persistence = _import_persistence()
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()

    class NotJSON:
        pass

    with pytest.raises(TypeError):
        persistence.save_state(strategies_dir, "x", {"bad": NotJSON()})


# ---------------------------------------------------------------------------
# Discover seam
# ---------------------------------------------------------------------------


_STRATEGY_OK = textwrap.dedent(
    """
    from framework.strategy.base import Strategy
    from framework.strategy.context import Context

    class MACrossStrategy:
        name = "ma_cross"

        def __init__(self, short: int = 5, long: int = 20):
            self.short = short
            self.long = long

        def generate(self, ctx):
            return {}
    """
).strip() + "\n"


_STRATEGY_BAD_IMPORT = textwrap.dedent(
    """
    from framework.strategy.base import Strategy
    from framework.data.adapter import AKShareAdapter   # name leak

    class Leak:
        name = "leak"

        def generate(self, ctx):
            return {}
    """
).strip() + "\n"


_STRATEGY_NO_NAME = textwrap.dedent(
    """
    from framework.strategy.base import Strategy

    class NoName:
        def __init__(self):
            pass

        def generate(self, ctx):
            return {}
    """
).strip() + "\n"


_STRATEGY_NO_GENERATE = textwrap.dedent(
    """
    from framework.strategy.base import Strategy

    class NoGen:
        name = "no_gen"
        def run(self, ctx):
            return {}
    """
).strip() + "\n"


_STRATEGY_DUPLICATE = textwrap.dedent(
    """
    from framework.strategy.base import Strategy

    class First:
        name = "ma_cross"

        def generate(self, ctx):
            return {}
    """
).strip() + "\n"


def _write(strategies_dir: Path, name: str, body: str) -> Path:
    strategies_dir.mkdir(exist_ok=True)
    p = strategies_dir / name
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def isolated_strategies(tmp_path, monkeypatch):
    """Make the project's `strategies/` package resolve to a temp directory so
    the real on-disk strategies/ doesn't interfere with these tests."""
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    # A real `__init__.py` makes the dir a proper Python package so the
    # discoverer can `importlib.import_module("strategies.<stem>")`. Tests
    # that want to assert "underscore-prefix is skipped" intentionally
    # don't have a __init__-prefixed strategy file.
    (strategies_dir / "__init__.py").write_text("", encoding="utf-8")

    import sys

    sys.path.insert(0, str(tmp_path))
    for mod_name in list(sys.modules):
        if mod_name == "strategies" or mod_name.startswith("strategies."):
            del sys.modules[mod_name]
    yield strategies_dir

    for mod_name in list(sys.modules):
        if mod_name == "strategies" or mod_name.startswith("strategies."):
            del sys.modules[mod_name]
    sys.path.remove(str(tmp_path))


def test_discover_finds_strategy(isolated_strategies: Path):
    from framework.strategy.discover import discover_strategies

    _write(isolated_strategies, "ma_cross.py", _STRATEGY_OK)
    registry = discover_strategies(strategies_dir=isolated_strategies)

    assert "ma_cross" in registry
    cls = registry["ma_cross"]
    assert cls.__name__ == "MACrossStrategy"
    instance = cls()
    assert instance.short == 5
    assert instance.long == 20


def test_discover_underscore_files_are_skipped(isolated_strategies: Path):
    from framework.strategy.discover import discover_strategies

    _write(isolated_strategies, "_helpers.py", _STRATEGY_OK)
    assert discover_strategies(strategies_dir=isolated_strategies) == {}


def test_discover_rejects_forbidden_imports(isolated_strategies: Path):
    from framework.strategy.discover import ForbiddenImportError, discover_strategies

    _write(isolated_strategies, "leak.py", _STRATEGY_BAD_IMPORT)

    with pytest.raises(ForbiddenImportError) as excinfo:
        discover_strategies(strategies_dir=isolated_strategies)
    assert "framework" in str(excinfo.value)


def test_discover_skips_class_without_name(isolated_strategies: Path):
    from framework.strategy.discover import discover_strategies

    _write(isolated_strategies, "noname.py", _STRATEGY_NO_NAME)
    assert discover_strategies(strategies_dir=isolated_strategies) == {}


def test_discover_skips_class_without_generate(isolated_strategies: Path):
    from framework.strategy.discover import discover_strategies

    _write(isolated_strategies, "nogen.py", _STRATEGY_NO_GENERATE)
    assert discover_strategies(strategies_dir=isolated_strategies) == {}


def test_discover_duplicate_name_raises(isolated_strategies: Path):
    from framework.strategy.discover import DuplicateStrategyNameError, discover_strategies

    _write(isolated_strategies, "a.py", _STRATEGY_DUPLICATE)
    _write(
        isolated_strategies,
        "b.py",
        _STRATEGY_DUPLICATE.replace("class First", "class Second"),
    )

    with pytest.raises(DuplicateStrategyNameError) as excinfo:
        discover_strategies(strategies_dir=isolated_strategies)
    assert "ma_cross" in str(excinfo.value)


# Decoupling — name-level allowlist (additional cases beyond `framework.data`)


_STRATEGY_BAD_NAME_FROM_STRATEGY_PKG = textwrap.dedent(
    """
    from framework.strategy.persistence import save_state   # forbidden name!

    class Leak:
        name = "leak"

        def generate(self, ctx):
            return {}
    """
).strip() + "\n"


_STRATEGY_USES_PACKAGE_REEXPORTS = textwrap.dedent(
    """
    # import the public-name form, not the submodule — allowed by the spec
    from framework.strategy import Strategy, Context, Position, Trade

    class PublicImport:
        name = "public"

        def generate(self, ctx):
            return {}
    """
).strip() + "\n"


def test_discover_rejects_name_leak_within_strategy_package(isolated_strategies: Path):
    """A strategy that ``from framework.strategy.persistence import …`` is
    rejected even though the package name is `framework.strategy.*`.
    Name-level allowlist only permits `Strategy / Context / Position / Trade`."""
    from framework.strategy.discover import ForbiddenImportError, discover_strategies

    _write(isolated_strategies, "leak.py", _STRATEGY_BAD_NAME_FROM_STRATEGY_PKG)
    with pytest.raises(ForbiddenImportError) as excinfo:
        discover_strategies(strategies_dir=isolated_strategies)
    assert "save_state" in str(excinfo.value)


def test_discover_allows_public_name_re_exports(isolated_strategies: Path):
    """``from framework.strategy import Strategy, Context, Position, Trade``
    is the canonical way strategies reference the API; must be accepted."""
    from framework.strategy.discover import discover_strategies

    _write(isolated_strategies, "public.py", _STRATEGY_USES_PACKAGE_REEXPORTS)
    reg = discover_strategies(strategies_dir=isolated_strategies)
    assert "public" in reg


# ---------------------------------------------------------------------------
# Context seam (data access + indicator dispatch)
# ---------------------------------------------------------------------------


class _BarsResult:
    def __init__(self, df):
        self.df = df


class _FakeAdapter:
    """Stand-in for AKShareAdapter — exposes a `get_bars(...)`-shaped method
    that returns an object with a `.df` attribute."""

    def __init__(self, df: pd.DataFrame | None = None):
        if df is None:
            df = _make_bars()
        self._df = df
        self.calls: list[dict] = []

    def get_bars(self, symbol: str, start: date, end: date, **kwargs):
        self.calls.append({"symbol": symbol, "start": start, "end": end, **kwargs})
        return _BarsResult(self._df)


def _make_bars(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": [10.0 + i for i in range(n)],
            "close": [10.5 + i for i in range(n)],
            "high": [11.0 + i for i in range(n)],
            "low": [9.5 + i for i in range(n)],
            "volume": [1000 * (i + 1) for i in range(n)],
            "amount": [10_000.0 * (i + 1) for i in range(n)],
        }
    )


def test_context_bars_routes_through_injected_adapter(tmp_path: Path):
    """`Context.bars(symbol, lookback)` should call adapter.get_bars with a
    window of `lookback` days ending at `ctx.now`."""
    adapter = _FakeAdapter()
    ctx = Context(
        now=date(2024, 1, 31),
        universe=["000001"],
        adapter=adapter,
    )
    df = ctx.bars("000001", lookback=10)

    assert df.equals(adapter._df)
    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert call["symbol"] == "000001"
    # Default adj/frequency flow through; test asserts a sane date window.
    assert call["end"] == date(2024, 1, 31)
    assert call["start"] == date(2024, 1, 21)  # 31 - 10


def test_context_price_returns_last_close(tmp_path: Path):
    adapter = _FakeAdapter(_make_bars(3))
    ctx = Context(now=date(2024, 1, 3), universe=["000001"], adapter=adapter)
    assert ctx.price("000001") == pytest.approx(12.5)


def test_context_indicator_dispatches_to_registry():
    """`ctx.indicator(name, df)` calls the registered pandas-ta wrapper. An
    unknown name raises UnknownIndicatorError (CAP-3: silent typos forbidden)."""
    bars = _make_bars()

    received: list[tuple] = []

    def fake_ma(df, length=20):
        received.append(("ma", length))
        return pd.Series(range(len(df)), name="ma")

    indicators_register("ma", fake_ma)
    try:
        adapter = _FakeAdapter()
        ctx = Context(now=date(2024, 1, 5), universe=["x"], adapter=adapter)

        out = ctx.indicator("ma", bars, length=5)
        assert received == [("ma", 5)]
        assert list(out) == [0, 1, 2, 3, 4]

        with pytest.raises(UnknownIndicatorError) as excinfo:
            ctx.indicator("not_a_real_indicator", bars)
        assert "not_a_real_indicator" in str(excinfo.value)
    finally:
        indicators_register("ma", None)  # teardown


def test_context_signal_validate_normalizes_to_dict():
    """Engines store weight values as floats; the helper verifies ranges."""
    from framework.strategy.context import validate_signal

    # OK: empty dict — engine interprets as "no rebalance"
    validate_signal({}, universe=["a", "b"])

    with pytest.raises(ValueError):
        validate_signal({"a": 1.5}, universe=["a"])  # > 1
    with pytest.raises(ValueError):
        validate_signal({"a": -0.1}, universe=["a"])  # < 0
    with pytest.raises(ValueError):
        validate_signal({"ghost": 0.5}, universe=["a"])  # outside universe
