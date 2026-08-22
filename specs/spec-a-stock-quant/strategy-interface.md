# Companion — Strategy Interface

This companion is part of `SPEC-a-stock-quant` (CAP-3, CAP-4). Locked in T3.

## Decoupling rule (constraint)

Strategy code **must** import only:

- `Strategy` (the base class)
- `Context` (the dataclass)

Anything else from `framework.*`, `engine.*`, `data.*`, `indicators.*` is **forbidden inside strategy files**. (Strategies may import third-party packages like `pandas`, `numpy`.) If you find yourself wanting framework internals in a strategy, lift them into the `Context` instead.

This is enforced at scan-time during `Strategy.discover()`: a strategy module is rejected if `inspect.getsource(module)` references any name not on the allowlist.

## `Strategy` base class

```python
from dataclasses import dataclass, field
from typing import Protocol
import pandas as pd


class Strategy(Protocol):
    name: str                                   # class attribute, mandatory

    def __init__(self, **params): ...           # all parameters as keyword defaults

    def generate(self, ctx: "Context") -> dict[str, float]:
        """Return {symbol: target_weight 0-1} for the current bar."""
        ...
```

- `name` is a class attribute (string, mandatory, unique across `strategies/`).
- `__init__` accepts **any keyword parameters with defaults**; type annotations become form fields.
- `generate()` is called per bar/day by the engine.
- Strategies are not required to subclass anything beyond `Protocol` — duck-typing is enough.

## `Context` dataclass (full)

```python
@dataclass
class Context:
    now: date
    universe: list[str]                                            # from strategy config (Open Question #1)

    # data access
    def bars(self, symbol: str, lookback: int = 60) -> pd.DataFrame:
        """Daily OHLCV+adj via DataAdapter. Cached for the bar."""
        ...

    def price(self, symbol: str) -> float:
        """Latest close; convenience over bars(symbol).iloc[-1].close."""
        ...

    # virtual book (per-strategy, the engine sets these per call)
    positions: dict[str, "Position"]                               # symbol -> Position
    cash: float
    portfolio_value: float                                         # sum(market value of positions) + cash

    # journal
    trades: list["Trade"]                                          # current virtual book's full trade history

    # per-strategy persistence
    state: dict                                                    # loaded from strategies/<name>.state.json; mutable

    # recommendations
    def indicator(self, name: str, *args, **kwargs) -> pd.Series | pd.DataFrame:
        """Thin wrapper over indicators/<name>."""
        ...


@dataclass
class Position:
    symbol: str
    qty: int
    avg_cost: float
    opened_at: date


@dataclass
class Trade:
    symbol: str
    side: str            # 'buy' | 'sell'
    qty: int
    price: float
    fee: float
    executed_at: date
```

## Signal contract — `target_position`

Output:

```python
{"510300": 0.4, "513500": 0.3, "511010": 0.3}
```

Mandatory rules:

- Values are in `[0, 1]` representing **target portfolio weight**.
- Sum of values may be `< 1` (rest is held as cash) but **never `> 1`** (no leverage in MVP).
- Returning an empty dict `{}` means "no rebalance needed" — engine skips.
- Returning a symbol not in `universe` is an error surfaced to the UI; not silently dropped.

Engine conversion (CAP-4 contract):

```
target_dollars[symbol] = target_weight[symbol] * portfolio_value
target_qty[symbol]    = floor(target_dollars / next_day_open)        # lot-aware rounding is out of MVP
delta_qty[symbol]     = target_qty - current_qty[symbol]
```

If `delta_qty > 0`: BUY `delta_qty` at next-day open (constraint: no same-day fills).
If `delta_qty < 0`: SELL `|delta_qty|` at next-day open.

## Discovery & registration protocol

```python
def discover_strategies() -> dict[str, type[Strategy]]:
    """Scan strategies/*.py; import; register subclasses of Strategy with class attr `name`."""
```

Rules:

- Files beginning with `_` are ignored.
- A class is "registerable" iff:
  1. it has a class-level `name: str`
  2. it has a `generate(self, ctx) -> dict` method
  3. its module passes the decoupling scan (no forbidden imports)
- Duplicate `name` values throw at discovery; first-wins policy is forbidden.
- Discovery runs at server startup **and** when the user clicks "刷新策略" on the Strategy page.
- Imported modules are cached in `sys.modules`; a "fresh import" toggle in UI forces `importlib.reload()`.

## Parameter persistence — `params.json`

For each registered strategy with class name `<name>`, the framework writes `strategies/<name>.params.json`:

```json
{
  "strategy_class": "strategies.ma_cross.MACrossStrategy",
  "strategy_name": "ma_cross",
  "params": {"short": 5, "long": 20, "drift_threshold": 0.05},
  "updated_at": "2026-08-22T10:00:00Z"
}
```

- Edited via the Streamlit form on the Strategy page (CAP-3).
- Loading: if `params.json` exists, instantiate with those values; otherwise instantiate with defaults from `__init__`.
- Writing: form "Save" overwrites the JSON atomically (write to `.tmp`, rename).
- File is gitignored.

## State persistence — `state.json`

- File `strategies/<name>.state.json` (also gitignored).
- Read once at startup, written on every `generate()` call (debounced is fine).
- Strategies freely mutate `ctx.state[key] = ...`; framework handles serialization.
- Reset = `rm strategies/<name>.state.json` (UI button offered).

## Activation guard (CAP-3 / CAP-2 intersection)

A strategy cannot enter "active" for the 15:30 schedule unless a backtest for it exists in the last 30 days. The check looks at an internal `backtests` table (see `backtest-engine.md`); "active" toggle is greyed out otherwise.

(This is a hard prerequisite per the Constraints section, not a UI warning.)
