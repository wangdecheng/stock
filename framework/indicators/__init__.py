"""framework/indicators/__init__.py

Thin indicator dispatcher (CAP-3, CAP-4). Strategies reach indicators only
through ``ctx.indicator(name, df, *args, **kwargs)``; this module is the
allowlist — unknown names raise. The actual ``pandas-ta`` wrappers live
alongside this seam (T12 ``indicators-and-metrics.md``); T2 ships an empty
registry plus a ``register()`` helper so tests and later indicator modules
have a stable surface.
"""

from __future__ import annotations

from typing import Callable, Optional

# name -> callable(df: pd.DataFrame, *args, **kwargs) -> pd.Series | pd.DataFrame
_REGISTRY: dict[str, Callable] = {}


def register(name: str, fn: Optional[Callable]) -> Callable:
    """Register or unregister an indicator.

    Tests call ``register("ma", fake_ma)`` to inject a stand-in and
    ``register("ma", None)`` to clear. Production module ``ma.py`` (T12)
    registers itself via ``from ..indicators import register`` at import
    time, so callers don't need to do anything.
    """
    if fn is None:
        _REGISTRY.pop(name, None)
        return lambda *a, **k: None
    _REGISTRY[name] = fn
    return fn


def allowed_names() -> list[str]:
    """Sorted list of currently-registered indicator names (CAP-3 UI surface)."""
    return sorted(_REGISTRY)


def compute(name: str, df, *args, **kwargs):
    """Resolve ``name`` through the registry and call it with ``df``.

    Raises ``KeyError`` for an unknown name; the wrapper ``Context.indicator``
    translates that into ``UnknownIndicatorError`` (a friendlier type for
    strategy authors).
    """
    fn = _REGISTRY[name]
    return fn(df, *args, **kwargs)


__all__ = ["register", "allowed_names", "compute"]
