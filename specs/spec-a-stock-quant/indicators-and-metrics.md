# Companion — Indicators & Metrics Modules

This companion is part of `SPEC-a-stock-quant` (CAP-2, CAP-4). Locked in T12 (tech indicators) and T2 (portfolio metrics).

## Two distinct modules

| Module | Purpose | Backing library |
|---|---|---|
| `indicators/` | Per-symbol technical indicators used by strategies | `pandas-ta` |
| `metrics/` | Portfolio-level returns / risk metrics used by engine & UI | `empyrical` |

These are kept separate deliberately. Strategies import `indicators.*`; engine and UI import `metrics.*`. Do not import the other from a "convenience" — coupling them restricts future replacements.

## Why pandas-ta, not TA-Lib (T12 rationale, repeated for downstream)

- TA-Lib has C bindings that fail to pip-install on Apple Silicon, Windows, and several Linux distros without conda or self-compilation.
- pandas-ta is pure Python, DataFrame-native, no installation risk.
- pandas-ta covers all A-share-common indicators used in the example strategies.

Trade-off: pandas-ta is slower than TA-Lib on very long histories (10y+ minute bars). For MVP daily bars on 1–3 years, performance is non-issue.

## `indicators/` — required function surface

```python
# signatures exposed to strategies via ctx.indicator(...) and direct import

def ma(df: pd.DataFrame, length: int = 20) -> pd.Series: ...
def ema(df: pd.DataFrame, length: int = 20) -> pd.Series: ...
def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Returns DataFrame with columns 'macd', 'hist', 'signal'."""
def bollinger(df: pd.DataFrame, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Returns DataFrame with columns 'lower', 'mid', 'upper'."""
def rsi(df: pd.DataFrame, length: int = 14) -> pd.Series: ...
def atr(df: pd.DataFrame, length: int = 14) -> pd.Series: ...
def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """Returns DataFrame with columns 'adx', 'plus_di', 'minus_di'."""
def kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """Returns DataFrame with columns 'k', 'd', 'j'."""
def obv(df: pd.DataFrame) -> pd.Series: ...
```

- All accept the AKShare-output DataFrame directly (no column rename).
- All return a `pd.Series` (single line) or `pd.DataFrame` (multiple lines), aligned to the input index.
- Internal implementation may use `pandas-ta` directly; the module's purpose is to give strategies a stable, slim surface that doesn't change if we swap out the underlying library later.

## Indicator registration is intentional

`ctx.indicator(name, *args, **kwargs)` is the strategy-facing entry. The framework validates `name` against the allowlist above; unknown names raise. This protects against silent typos.

## `metrics/` — required output schema

See `backtest-engine.md` § Metrics for the dict. The module exposes only:

```python
def compute(equity: "Equity", *, risk_free: float = 0.02) -> dict: ...
```

No other shapes — UI's six metric cards read fixed keys:

```
total_return, annualized, sharpe, max_drawdown, calmar, win_rate, profit_loss_ratio
```

## Tests (mandatory)

- `test_indicators_match_pandas_ta_reference.py`: 1 test per function, asserts output equals `pandas-ta` direct call for a synthetic OHLCV input.
- `test_metrics_match_empyrical_reference.py`: synthetic daily returns, asserts the dict matches empyrical's reference outputs to 1e-6.

These two test files are the contract for the modules; breaking them is a flag for vendor upgrades.
