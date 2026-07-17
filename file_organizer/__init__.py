"""Safe, deterministic, transaction-backed file organization."""

from __future__ import annotations

__version__ = "2.0.0"

from .duplicates import find_duplicates
from .executor import apply_plan, apply_undo
from .history import create_undo_plan, list_history
from .planner import create_plan
from .rules import RuleSet, default_rules, load_rules

__all__ = [
    "RuleSet",
    "__version__",
    "apply_plan",
    "apply_undo",
    "create_plan",
    "create_undo_plan",
    "default_rules",
    "find_duplicates",
    "list_history",
    "load_rules",
]
