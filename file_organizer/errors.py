"""Domain-specific exceptions for :mod:`file_organizer`."""

from __future__ import annotations

from pathlib import Path


class OrganizerError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigurationError(OrganizerError):
    """Raised when a rules file is malformed or unsafe."""


class SafetyError(OrganizerError):
    """Raised when an operation would cross a safety boundary."""


class PlanningError(OrganizerError):
    """Raised when a deterministic plan cannot be produced."""


class ConflictError(OrganizerError):
    """Raised when filesystem state conflicts with a plan or undo."""

    def __init__(self, message: str, conflicts: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicts = conflicts or []


class HistoryError(OrganizerError):
    """Raised when operation history is absent, corrupt, or unsafe."""


class TransactionError(OrganizerError):
    """Raised when an apply fails, including rollback details."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: str | None = None,
        manifest_path: Path | None = None,
        rollback_succeeded: bool | None = None,
        rollback_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.manifest_path = manifest_path
        self.rollback_succeeded = rollback_succeeded
        self.rollback_count = rollback_count
