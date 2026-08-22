"""framework/strategy/discover.py

Scan ``strategies/*.py`` for ``Strategy`` subclasses / duck-types and
register them. Spec: ``strategy-interface.md`` §"Discovery & registration
protocol" — files starting with ``_`` are ignored; a class is registerable
iff it has a class-level ``name: str`` AND a ``generate(self, ctx)`` method
AND the module passes the decoupling scan.

Decoupling guard
---------------
The spec phrasing is "rejected if ``inspect.getsource(module)`` references
any name not on the allowlist." We use ``ast`` instead because AST ignores
comments / docstrings / string literals — regex-on-source would false-
positive on docstrings mentioning ``akshare`` or ``pandas-ta``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Optional

# Names a strategy file is allowed to import from `framework.strategy.*`.
# Per spec (strategy-interface.md §"Decoupling rule"), the only allowed
# imports are `Strategy` (base class) and `Context` (dataclass). Position /
# Trade are value-types reachable through Context, so they ride along.
# Anything else (engine internals, persistence helper, indicator registry)
# is forbidden.
_ALLOWED_STRATEGY_NAMES = frozenset({"Strategy", "Context", "Position", "Trade"})

# The framework.strategy package itself is the canonical import path; the
# spec doesn't mandate which submodule it routes through.
_FW_STRATEGY_PREFIXES = (
    "framework.strategy.",
    "framework.strategy",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DiscoverError(Exception):
    """Base for discovery-time errors."""


class DuplicateStrategyNameError(DiscoverError):
    """Two files in strategies/ claim the same ``name = "..."``."""


class ForbiddenImportError(DiscoverError):
    """A strategy module imports something outside the allowlist."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_strategies(
    strategies_dir: Optional[Path | str] = None,
    *,
    force_reload: bool = False,
) -> dict[str, type]:
    """Scan ``strategies_dir`` (default: project's ``strategies/`` next to
    the repo root) for ``.py`` files and return ``{name: class}``.

    Rules enforced (per spec):

      * Files beginning with ``_`` are ignored.
      * A class is registerable iff it has a class-level ``name: str`` AND a
        method ``generate(self, ctx)``.
      * Duplicate ``name`` → ``DuplicateStrategyNameError``. First-wins is
        forbidden by spec.
      * Modules that import anything outside the framework-strategy
        allowlist raise ``ForbiddenImportError``.
      * Imported modules are cached in ``sys.modules``; pass
        ``force_reload=True`` to call ``importlib.reload()`` on each so a UI
        "refresh strategies" button can pick up edits without restart.
    """
    if strategies_dir is None:
        # Project layout: this file lives at <repo>/framework/strategy/discover.py
        # so the strategies package is at <repo>/strategies/.
        strategies_dir = Path(__file__).resolve().parents[2] / "strategies"
    strategies_dir = Path(strategies_dir)
    if not strategies_dir.is_dir():
        return {}

    # Make strategies importable. We put the directory one level up on path
    # so ``importlib.import_module("ma_cross")`` resolves to that file.
    pkg_root = strategies_dir.parent
    pkg_root_str = str(pkg_root)
    if pkg_root_str not in sys.path:
        sys.path.insert(0, pkg_root_str)

    registry: dict[str, type] = {}
    for path in sorted(strategies_dir.glob("*.py")):
        stem = path.stem
        # Skip __init__.py (package marker) and underscore-prefixed helpers.
        if stem == "__init__" or stem.startswith("_"):
            continue

        module_name = f"{strategies_dir.name}.{stem}"

        # Drop the cached strategy module so edits are picked up on the
        # next scan. Without this, a "refresh strategies" button would
        # always return the first-imported version.
        for cache_name in (stem, module_name):
            sys.modules.pop(cache_name, None)

        try:
            module = importlib.import_module(module_name)
        except Exception:
            # Files that import third-party deps unavailable in this Python
            # fail at import time; surface as discovery errors rather than
            # silently swallowing.
            raise

        if force_reload:
            importlib.reload(module)

        _check_decoupling(module, path)

        for cls in _iter_strategy_classes(module):
            name = getattr(cls, "name", None)
            if not isinstance(name, str) or not name:
                # Class without a string ``name`` is not registerable; skip.
                continue
            if name in registry:
                raise DuplicateStrategyNameError(
                    f"strategy name {name!r} is registered twice "
                    f"(first: {registry[name].__module__}.{registry[name].__name__}, "
                    f"second: {module_name}.{cls.__name__})"
                )
            registry[name] = cls

    return registry


# ---------------------------------------------------------------------------
# Decoupling scan (AST)
# ---------------------------------------------------------------------------


def _check_decoupling(module, path: Path) -> None:
    """Reject the module if it imports anything not on the allowlist.

    Spec phrasing (strategy-interface.md): "rejected if the source
    references any name not on the allowlist." Specifically:

      * Third-party / stdlib imports are allowed (pandas, numpy, ...).
      * ``from framework.strategy import Strategy, Context`` is allowed —
        only the names ``Strategy``, ``Context``, ``Position``, ``Trade``
        are reachable from inside strategy files.
      * Imports of any other framework module are forbidden (data, engine,
        indicators, persistence, etc.).

    We implement this name-aware — AST-level names, not regex on source —
    so comments and docstrings that happen to mention forbidden names
    don't trigger a false positive.
    """
    source = _read_source(path)
    tree = ast.parse(source, filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # ``import framework.strategy.discover`` — reject outright since
            # the bare import doesn't reference Strategy / Context at all.
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "framework" and not _is_strategy_only(alias.name):
                    raise ForbiddenImportError(
                        f"{path}:{lineno_of(node)} imports {alias.name!r}; "
                        f"strategies may only import names from "
                        f"{sorted(_ALLOWED_STRATEGY_NAMES)} via "
                        f"framework.strategy.*"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative; sibling strategies are fine
            module_name = node.module or ""
            top = module_name.split(".")[0]
            if top != "framework":
                continue  # third-party / stdlib
            if not module_name.startswith(_FW_STRATEGY_PREFIXES):
                raise ForbiddenImportError(
                    f"{path}:{lineno_of(node)} imports from {module_name!r}; "
                    f"strategies may only import from framework.strategy.*"
                )
            for alias in node.names:
                if alias.name == "*":
                    raise ForbiddenImportError(
                        f"{path}:{lineno_of(node)} star-imports from "
                        f"{module_name!r}; explicit names required"
                    )
                if alias.name not in _ALLOWED_STRATEGY_NAMES:
                    raise ForbiddenImportError(
                        f"{path}:{lineno_of(node)} imports {alias.name!r} from "
                        f"{module_name!r}; only "
                        f"{sorted(_ALLOWED_STRATEGY_NAMES)} are allowed"
                    )


def _is_strategy_only(dotted: str) -> bool:
    """True iff ``dotted`` names a module under framework.strategy that
    contains only the allowed names. ``framework.strategy.persistence``
    doesn't satisfy this."""
    if not dotted.startswith(_FW_STRATEGY_PREFIXES):
        return False
    rest = dotted.split(".", 2)[-1] if dotted.startswith("framework.strategy.") else ""
    return rest in {"base", "context", ""}


def lineno_of(node: ast.AST) -> int:
    return getattr(node, "lineno", 0)


def _read_source(path: Path) -> str:
    """Prefer the live module's source (already loaded into sys.modules),
    fall back to disk. The live source reflects the imported object — the
    disk file may be older if the file was edited between import and scan.
    """
    # Cheap check: if a sibling loader has the file content, use it via
    # inspect.getsource on the module name. Simpler: read disk; the file
    # was just imported, so disk and module-source agree.
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Class discovery
# ---------------------------------------------------------------------------


def _iter_strategy_classes(module):
    """Yield every class in ``module`` that has both a string ``name`` class
    attribute and a callable ``generate`` method. Duck-typing only;
    subclasses of ``Strategy`` are not required."""
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        name_attr = obj.__dict__.get("name")  # only class-level, not inherited
        if not isinstance(name_attr, str) or not name_attr:
            continue
        if not callable(getattr(obj, "generate", None)):
            continue
        yield obj


__all__ = [
    "discover_strategies",
    "DuplicateStrategyNameError",
    "ForbiddenImportError",
    "DiscoverError",
]
