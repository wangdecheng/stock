"""framework/backtest/engine.py

T3 — backtest event loop. Spec: ``specs/spec-a-stock-quant/backtest-engine.md``.

Event loop (canonical, per spec §"Event loop (canonical order)"):

    for t in calendar:
        1. sync state — build Context reflecting positions/cash as-of t-1 close
        2. strategy.generate(ctx) -> {symbol: target_weight} in [0, 1]
        3. diff — target_dollars = weight * portfolio_value; delta_qty from book
        4. fill at next-day open — orders queued today fill on t+1.open
        5. update virtual book via position-schema.md weighted-average cost basis
        6. append (portfolio_value, cash, benchmark_value) for date t+1
        7. (state.json writes happen every N bars; left to caller)

Orders queued on the **last** bar are dropped — no forward-equity leakage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from framework.strategy.context import Context, validate_signal


# ---------------------------------------------------------------------------
# Dataclasses (canonical per spec)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fill:
    """A single execution in the backtest.

    ``date`` is the **fill** date (= next-day open after the signal bar).
    """

    date: date
    symbol: str
    side: str          # 'buy' | 'sell'
    qty: int
    price: float       # open of fill date
    fee: float


@dataclass
class EngineConfig:
    """Engine knobs callers can override. Defaults match the SPEC table in
    ``backtest-engine.md`` §"Matching assumptions (simplified, MVP, locked)"."""

    commission: float = 0.0003
    adj: str = "qfq"
    frequency: str = "daily"
    lookback: int = 60      # bars fetched by ``ctx.bars(symbol, lookback)``


@dataclass
class Equity:
    """Per-run output (spec §"Equity object")."""

    dates: list[date]
    portfolio_value: list[float]
    benchmark_value: list[float]
    cash: list[float]
    fills: list[Fill]
    strategy_name: str
    start: date
    end: date
    initial_cash: float
    final_value: float


# ---------------------------------------------------------------------------
# Virtual book (per-run; in-memory)
# ---------------------------------------------------------------------------


@dataclass
class _BookPosition:
    qty: int
    avg_cost: float
    opened_at: date


@dataclass
class _VirtualBook:
    """In-memory positions + cash. Multi-lot cost basis per
    ``specs/spec-a-stock-quant/position-schema.md`` §"Cost-basis algorithm"."""

    cash: float
    positions: dict[str, _BookPosition] = field(default_factory=dict)

    def portfolio_value(self, prices: dict[str, float]) -> float:
        equity = self.cash
        for sym, pos in self.positions.items():
            equity += pos.qty * prices.get(sym, pos.avg_cost)
        return equity

    def apply(self, fill: Fill) -> None:
        """Apply a Fill using weighted-average cost basis.

        Multi-lot buy: new_avg = (old_qty*old_avg + new_qty*new_price + fee) /
                              (old_qty + new_qty)
        Multi-lot sell: qty decreases; avg_cost stays unchanged.
        """
        pos = self.positions.get(fill.symbol)
        if fill.side == "buy":
            if pos is None:
                pos = _BookPosition(qty=0, avg_cost=0.0, opened_at=fill.date)
                self.positions[fill.symbol] = pos
            old_qty, old_avg = pos.qty, pos.avg_cost
            new_total = old_qty * old_avg + fill.qty * fill.price + fill.fee
            new_qty = old_qty + fill.qty
            pos.qty = new_qty
            pos.avg_cost = new_total / new_qty if new_qty else 0.0
            if old_qty == 0:
                pos.opened_at = fill.date
            self.cash -= fill.qty * fill.price + fill.fee
        elif fill.side == "sell":
            if pos is None:
                return
            pos.qty -= fill.qty
            self.cash += fill.qty * fill.price - fill.fee
        else:
            raise ValueError(f"unknown fill side: {fill.side!r}")


@dataclass
class _PendingOrder:
    symbol: str
    side: str          # 'buy' | 'sell'
    qty: int


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Engine:
    """Daily-resolution event-loop runner.

    Construction parameters
    -----------------------
    strategy
        Already-instantiated strategy with ``generate(ctx) -> dict`` method.
    universe
        Symbols the strategy is allowed to mention. Used both for the signal
        validator and for ``ctx.universe``.
    adapter
        Source of strategy bars (the ``DataAdapter`` or a fake in tests).
    benchmark_adapter, benchmark_symbol
        Separate source for the benchmark series (default 沪深300 = '000300').
    calendar
        Ordered list of trading days. Engine iterates this exactly.
    config
        Optional ``EngineConfig`` knobs (commission, adj, frequency, lookback).
    """

    def __init__(
        self,
        *,
        strategy: Any,
        universe: list[str],
        adapter: Any,
        benchmark_adapter: Any = None,
        benchmark_symbol: str = "000300",
        calendar: list[date],
        config: Optional[EngineConfig] = None,
    ):
        if not calendar:
            raise ValueError("calendar must contain at least one trading day")
        self.strategy = strategy
        self.universe = list(universe)
        self.adapter = adapter
        self.benchmark_adapter = benchmark_adapter or adapter
        self.benchmark_symbol = benchmark_symbol
        self.calendar = list(calendar)
        self.config = config or EngineConfig()

    # ----- public ----------------------------------------------------------

    def run(self, *, initial_cash: float, commission: Optional[float] = None) -> Equity:
        """Run the event loop end-to-end and return an Equity object."""
        commission_rate = self.config.commission if commission is None else commission
        book = _VirtualBook(cash=initial_cash)
        benchmark_series = self._benchmark_closes()

        eq_dates: list[date] = []
        eq_pv: list[float] = []
        eq_bench: list[float] = []
        eq_cash: list[float] = []
        eq_fills: list[Fill] = []

        pending_by_date: dict[date, list[_PendingOrder]] = {}

        last_bar = self.calendar[-1]

        for t_idx, t in enumerate(self.calendar):
            # (4) execute orders queued yesterday at today's open
            if t in pending_by_date:
                today_opens = self._opens_at(t)
                for order in pending_by_date.pop(t):
                    fill_price = today_opens.get(order.symbol)
                    if fill_price is None:
                        continue  # symbol wasn't traded (no fill)
                    proceeds = order.qty * fill_price
                    fee = proceeds * commission_rate
                    fill = Fill(
                        date=t, symbol=order.symbol, side=order.side,
                        qty=order.qty, price=fill_price, fee=fee,
                    )
                    book.apply(fill)
                    eq_fills.append(fill)

            # Today's snapshot reflects just-applied fills.
            today_closes = self._closes_at(t)
            bench_price = self._nearest_benchmark(benchmark_series, t)
            pv = book.portfolio_value(today_closes)

            eq_dates.append(t)
            eq_pv.append(pv)
            eq_cash.append(book.cash)
            eq_bench.append(bench_price if bench_price is not None else pv)

            # (1) sync state — build Context reflecting post-fill book.
            ctx_positions = {
                sym: type("P", (), {
                    "symbol": sym, "qty": p.qty, "avg_cost": p.avg_cost,
                    "opened_at": p.opened_at,
                })()
                for sym, p in book.positions.items()
            }

            ctx = Context(
                now=t,
                universe=self.universe,
                adapter=self.adapter,
                positions=ctx_positions,
                cash=book.cash,
                portfolio_value=pv,
                state={},
            )

            # (2) strategy.generate(ctx)
            signal = self.strategy.generate(ctx)

            # (2a) validate — out-of-universe / out-of-range must surface.
            validate_signal(signal, self.universe)

            # (3) diff at today's close (spec: "using today's close")
            if t == last_bar:
                continue  # last-bar orders dropped; no forward-equity leakage
            next_day = self.calendar[t_idx + 1]
            queued = self._diff_orders(
                signal=signal,
                closes=today_closes,
                book=book,
            )
            if queued:
                pending_by_date.setdefault(next_day, []).extend(queued)

        return Equity(
            dates=eq_dates,
            portfolio_value=eq_pv,
            benchmark_value=eq_bench,
            cash=eq_cash,
            fills=eq_fills,
            strategy_name=getattr(self.strategy, "name", type(self.strategy).__name__),
            start=self.calendar[0],
            end=self.calendar[-1],
            initial_cash=initial_cash,
            final_value=eq_pv[-1] if eq_pv else initial_cash,
        )

    # ----- helpers --------------------------------------------------------

    def _diff_orders(
        self,
        *,
        signal: dict[str, float],
        closes: dict[str, float],
        book: _VirtualBook,
    ) -> list[_PendingOrder]:
        orders: list[_PendingOrder] = []
        for sym, weight in signal.items():
            close = closes.get(sym)
            if close is None or close <= 0:
                continue
            target_dollars = weight * book.portfolio_value(closes)
            target_qty = int(target_dollars // close)   # floor (spec)
            current_qty = int(book.positions.get(sym, _BookPosition(0, 0.0, None)).qty)
            delta = target_qty - current_qty
            if delta > 0:
                orders.append(_PendingOrder(symbol=sym, side="buy", qty=delta))
            elif delta < 0:
                orders.append(_PendingOrder(symbol=sym, side="sell", qty=-delta))
        return orders

    def _closes_at(self, day: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym in self.universe:
            try:
                out[sym] = self._single_close(sym, day)
            except Exception:
                continue
        return out

    def _single_close(self, symbol: str, day: date) -> float:
        result = self.adapter.get_bars(
            symbol,
            start=day - timedelta(days=10),
            end=day,
            adj=self.config.adj,
            frequency=self.config.frequency,
        )
        df = result.df
        if df.empty:
            raise ValueError(f"no bars for {symbol} on {day}")
        return float(df.iloc[-1]["close"])

    def _opens_at(self, day: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym in self.universe:
            try:
                result = self.adapter.get_bars(
                    sym, start=day - timedelta(days=10), end=day,
                    adj=self.config.adj, frequency=self.config.frequency,
                )
                df = result.df
                if df.empty:
                    continue
                out[sym] = float(df.iloc[-1]["open"])
            except Exception:
                continue
        return out

    def _benchmark_closes(self) -> list[tuple[date, float]]:
        """One-shot fetch of the benchmark close series over the calendar
        window. Used to align benchmark_value per day without hitting the
        adapter every bar."""
        start, end = self.calendar[0], self.calendar[-1]
        result = self.benchmark_adapter.get_bars(
            self.benchmark_symbol,
            start=start - timedelta(days=10),
            end=end,
            adj=self.config.adj,
            frequency=self.config.frequency,
        )
        df = result.df
        out: list[tuple[date, float]] = []
        for _, row in df.iterrows():
            d = row["date"]
            if hasattr(d, "date"):
                d = d.date()
            out.append((d, float(row["close"])))
        out.sort(key=lambda x: x[0])
        return out

    @staticmethod
    def _nearest_benchmark(
        series: list[tuple[date, float]],
        target: date,
    ) -> Optional[float]:
        """Return the latest benchmark close on or before ``target``.

        Uses ``<=`` so same-day close is used (matches the spec's
        'parallel: same-day close of chosen benchmark')."""
        result = None
        for d, price in series:
            if d <= target:
                result = price
            else:
                break
        return result


__all__ = ["Engine", "EngineConfig", "Equity", "Fill"]
