"""framework/backtest/metrics.py

T3 — empyrical wrapper per
``specs/spec-a-stock-quant/backtest-engine.md`` §"Metrics" and
``indicators-and-metrics.md`` §"`metrics/` — required output schema".

The UI reads fixed keys (`st.metric` cards); the dict is the contract.
Win rate / profit-loss ratio are computed locally from fills (empyrical
doesn't know about executions).
"""

from __future__ import annotations

import pandas as pd

import empyrical as ep

from framework.backtest.engine import Equity


def compute(equity: Equity, *, risk_free: float = 0.02) -> dict:
    """Return the canonical 7-key metrics dict for a backtest.

    Keys (fixed by SPEC):
        total_return, annualized, sharpe, max_drawdown, calmar,
        win_rate, profit_loss_ratio

    ``win_rate`` / ``profit_loss_ratio`` come from fills (round-trip trades),
    not from daily returns — empyrical has no notion of an execution. We
    compute them unconditionally so a backtest with only 1 bar but real
    fills still surfaces trade-level PnL.
    """
    win_rate, pl_ratio = _win_pl_summary(equity.fills)

    pv = pd.Series(
        equity.portfolio_value,
        index=pd.to_datetime(equity.dates),
        dtype=float,
    )
    returns = pv.pct_change().dropna()

    if returns.empty:
        # Returns-derived metrics are meaningless with <2 bars. UI is expected
        # to render zero / "—"; the trade-level metrics remain real.
        return {
            "total_return": 0.0,
            "annualized": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "win_rate": win_rate,
            "profit_loss_ratio": pl_ratio,
        }

    total_return = float(ep.cum_returns(returns).iloc[-1])
    annualized = float(ep.annual_return(returns))
    sharpe = float(ep.sharpe_ratio(returns, risk_free=risk_free))
    max_dd = float(ep.max_drawdown(returns))
    raw_calmar = float(ep.calmar_ratio(returns)) if not _is_zero(pv.iloc[0]) else 0.0
    # Calmar is annualized return / |max drawdown|. When there is no
    # drawdown, empyrical returns NaN (0/0). The UI can't render NaN, and
    # "no drawdown" is best surfaced as 0 / "—" in the card.
    import math
    calmar = 0.0 if math.isnan(raw_calmar) else raw_calmar

    return {
        "total_return": total_return,
        "annualized": annualized,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "profit_loss_ratio": pl_ratio,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _win_pl_summary(fills) -> tuple[float, float]:
    """Compute win-rate and profit/loss ratio from the fill list.

    A "round-trip" is a buy followed by a sell (same symbol, sells first
    consume the open quantity). For MVP, we treat pairs as FIFO: each buy
    is matched against subsequent sells in chronological order. This is
    enough for the six UI cards and avoids pulling in a full accounting
    stack.

    Returns ``(0.0, 0.0)`` when there are no closed round-trips yet.
    """
    # FIFO ladder per symbol
    open_lots: dict[str, list[tuple[int, float]]] = {}
    closed: list[float] = []

    for fill in fills:
        lots = open_lots.setdefault(fill.symbol, [])
        if fill.side == "buy":
            lots.append((fill.qty, fill.price + fill.fee / max(fill.qty, 1)))
        elif fill.side == "sell":
            remaining = fill.qty
            sell_net = fill.price - fill.fee / max(fill.qty, 1)
            while remaining > 0 and lots:
                lot_qty, lot_cost = lots[0]
                take = min(remaining, lot_qty)
                # Realized PnL on the matched chunk
                realized = (sell_net - lot_cost) * take
                closed.append(realized)
                remaining -= take
                if take == lot_qty:
                    lots.pop(0)
                else:
                    lots[0] = (lot_qty - take, lot_cost)

    if not closed:
        return 0.0, 0.0

    wins = [r for r in closed if r > 0]
    losses = [r for r in closed if r <= 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")
    return float(win_rate), float(pl_ratio)


def _is_zero(x: float) -> bool:
    return abs(float(x)) < 1e-12


__all__ = ["compute"]
