"""framework/persistence/__init__.py — public surface for SQLite + repo."""

from framework.persistence.config import delete_config, get_config, set_config
from framework.persistence.db import ensure_schema, open_db
from framework.persistence.positions import (
    list_real_positions,
    real_avg_cost,
    real_position,
)
from framework.persistence.repo import (
    DEFAULT_COMMISSION,
    DEFAULT_INITIAL_CASH,
    AlreadyAppliedError,
    InsufficientCashError,
    MissingTargetPriceError,
    RealTrade,
    RepoError,
    StrategySuggestion,
    SuggestionNotFoundError,
    VirtualBook,
    VirtualBookNotFoundError,
    VirtualPosition,
    adjust_virtual_cash,
    apply_suggestions_to_book,
    delete_real_trade,
    ensure_virtual_book,
    get_virtual_book,
    insert_suggestion,
    insert_suggestions,
    list_pending_suggestions,
    list_real_trades,
    list_suggestions,
    list_virtual_positions,
    record_real_trade,
    set_virtual_initial_cash,
    update_real_trade,
)

__all__ = [
    # db
    "open_db",
    "ensure_schema",
    # positions
    "real_position",
    "real_avg_cost",
    "list_real_positions",
    # config (T5)
    "get_config",
    "set_config",
    "delete_config",
    # repo
    "DEFAULT_COMMISSION",
    "DEFAULT_INITIAL_CASH",
    "AlreadyAppliedError",
    "InsufficientCashError",
    "MissingTargetPriceError",
    "RepoError",
    "SuggestionNotFoundError",
    "VirtualBookNotFoundError",
    "RealTrade",
    "VirtualBook",
    "VirtualPosition",
    "StrategySuggestion",
    "record_real_trade",
    "update_real_trade",
    "delete_real_trade",
    "list_real_trades",
    "get_virtual_book",
    "ensure_virtual_book",
    "adjust_virtual_cash",
    "set_virtual_initial_cash",
    "list_virtual_positions",
    "insert_suggestion",
    "insert_suggestions",
    "list_pending_suggestions",
    "list_suggestions",
    "apply_suggestions_to_book",
]