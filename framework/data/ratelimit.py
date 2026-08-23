"""framework/data/ratelimit.py

Hard rate-limit wall for DataAdapter (CAP-1, Constraint 2).

Eastmoney (AKShare upstream) blocks the source IP at 20 req/min (T4-research).
Anything over that ceiling risks a temporary or permanent ban.

Implementation note on the limiter choice
-----------------------------------------
The SPEC describes a "token bucket" but the burst-test acceptance is the
binding invariant: "≤ 20 calls in any 60 s sliding window". A token bucket
with capacity=20 and refill=20/60s admits 40 calls in the first 60 s window
(20 burst + 20 refilled), so it does NOT satisfy the acceptance.

We therefore use a strict sliding-window limiter, which guarantees the
invariant exactly. `TokenBucket` remains in this module as a primitive that
future per-endpoint limits or non-strict ceilings may want — but the global
limiter below is a `SlidingWindowLimiter`.

One process-wide limiter is correct for MVP: the whole app runs on one cloud
server with one IP.
"""

from __future__ import annotations

import collections
import functools
import threading
import time
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable[..., object])


class RateLimitExceeded(Exception):
    """Raised when ``ratelimit.acquire(timeout=...)`` exhausts its wait budget.

    The caller (page / engine) decides whether to surface this to the user
    or silently fall back to a stale-cache path. We used to block forever
    (``acquire(timeout=None)``), which left ``st.spinner`` spinning until
    upstream rate-limit windows aged out — typically ~60 s, which is what
    the stock-detail page was exhibiting in production.
    """


# ---------------------------------------------------------------------------
# TokenBucket — primitive, kept available for non-strict use cases
# ---------------------------------------------------------------------------


class TokenBucket:
    """Thread-safe blocking token bucket (primitive).

    ``acquire()`` blocks (up to ``timeout`` seconds; None = forever) until tokens
    are available. Allows bursts up to ``capacity`` then paces at the refill
    rate; NOT a strict "N per window" guarantee.
    """

    def __init__(self, capacity: int, refill_per_second: float):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill_per_second,
            )
            self._last_refill = now

    def acquire(self, tokens: int = 1, timeout: float | None = None) -> bool:
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if tokens > self._capacity:
            raise ValueError("requested tokens exceed bucket capacity")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    self._cond.notify_all()
                    return True
                needed = tokens - self._tokens
                wait = needed / self._refill_per_second
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    wait = min(wait, remaining)
                self._cond.wait(wait)

    @property
    def available(self) -> float:
        with self._lock:
            self._refill_locked()
            return self._tokens


# ---------------------------------------------------------------------------
# SlidingWindowLimiter — strict invariant used by the global limit
# ---------------------------------------------------------------------------


class SlidingWindowLimiter:
    """Strict sliding-window rate limiter.

    Invariant: in any window of ``window_seconds`` seconds, no more than
    ``max_calls`` are admitted.

    Implemented as a deque of recent acquisition timestamps protected by a
    `Condition` for blocking wait. Threads that find the window full block
    until the oldest call ages out.
    """

    def __init__(self, max_calls: int, window_seconds: float):
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_calls = max_calls
        self._window = float(window_seconds)
        self._calls: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def acquire(self, timeout: float | None = None) -> bool:
        """Block until a slot is available. Returns True if acquired, False if
        timed out."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                now = time.monotonic()
                # Drop timestamps that have aged out of the window
                while self._calls and self._calls[0] <= now - self._window:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    self._cond.notify_all()
                    return True
                # Wait for the oldest in-window timestamp to age out
                wait = self._calls[0] + self._window - now
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    wait = min(wait, remaining)
                self._cond.wait(max(0.0, wait))


# ---------------------------------------------------------------------------
# Global limiter + decorator
# ---------------------------------------------------------------------------


# CAP-1 constraint: 20 req/min/IP. Strict "≤20 in any 60 s sliding window".
GLOBAL_LIMITER = SlidingWindowLimiter(max_calls=20, window_seconds=60.0)


# Hard cap on how long ``ratelimit`` will block waiting for a slot. Without
# this bound, a saturated limiter blocks forever (the original
# ``timeout=None`` default) and the page spinner is stuck until the oldest
# in-window timestamp ages out — observed as a steady 60 s+ on the stock
# detail page when other processes had already eaten the quota.
#
# 30 s leaves room for two full upstream timeouts (eastmoney + Tencent, each
# 10 s) while still surfacing a useful error to the UI. Pages opt to either
# show a "rate-limited, try again in N seconds" warning or transparently
# fall back to the parquet cache (the latter requires the caller to inspect
# the exception and re-shape the request).
RATELIMIT_ACQUIRE_TIMEOUT_SECONDS = 30.0


def ratelimit(func: F) -> F:
    """Decorator that blocks on the global sliding-window limiter before
    invoking ``func``. Works on plain functions and bound/unbound methods.

    Raises ``RateLimitExceeded`` if no slot becomes available within
    ``RATELIMIT_ACQUIRE_TIMEOUT_SECONDS`` so callers can decide whether to
    surface a warning or fall back to stale data.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not GLOBAL_LIMITER.acquire(timeout=RATELIMIT_ACQUIRE_TIMEOUT_SECONDS):
            raise RateLimitExceeded(
                f"upstream rate limit (20 req/min) not released within "
                f"{RATELIMIT_ACQUIRE_TIMEOUT_SECONDS:.0f}s; try again later"
            )
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


# Backwards-compat alias: the previous name was GLOBAL_BUCKET. Keep the symbol
# so external imports do not break; it now points at the limiter.
GLOBAL_BUCKET = GLOBAL_LIMITER  # type: ignore[assignment]