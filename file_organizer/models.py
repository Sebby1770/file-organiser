"""Immutable data models shared by planning, execution, and the CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """Content identity and useful race-detection metadata for one file."""

    size: int
    sha256: str
    mtime_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "sha256": self.sha256,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FileFingerprint":
        if not isinstance(value, dict):
            raise TypeError("fingerprint must be an object")
        size = value.get("size")
        sha256 = value.get("sha256")
        mtime_ns = value.get("mtime_ns")
        if not isinstance(size, int) or size < 0:
            raise TypeError("fingerprint.size must be a non-negative integer")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(char not in "0123456789abcdef" for char in sha256)
        ):
            raise TypeError("fingerprint.sha256 must be a lowercase SHA-256 digest")
        if not isinstance(mtime_ns, int) or mtime_ns < 0:
            raise TypeError("fingerprint.mtime_ns must be a non-negative integer")
        return cls(size=size, sha256=sha256, mtime_ns=mtime_ns)


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    size: int
    sha256: str
    files: tuple[Path, ...]

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "size": self.size,
            "sha256": self.sha256,
            "files": [_relative(path, root) for path in self.files],
        }


@dataclass(frozen=True, slots=True)
class DuplicateReport:
    root: Path
    recursive: bool
    scanned_files: int
    groups: tuple[DuplicateGroup, ...]
    skipped: tuple["SkippedItem", ...]

    def to_dict(self) -> dict[str, Any]:
        duplicate_files = sum(len(group.files) for group in self.groups)
        reclaimable_bytes = sum(
            group.size * (len(group.files) - 1) for group in self.groups
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "duplicate-report",
            "root": str(self.root),
            "recursive": self.recursive,
            "summary": {
                "scanned_files": self.scanned_files,
                "duplicate_groups": len(self.groups),
                "duplicate_files": duplicate_files,
                "reclaimable_bytes": reclaimable_bytes,
                "skipped": len(self.skipped),
            },
            "duplicates": [group.to_dict(self.root) for group in self.groups],
            "skipped": [item.to_dict(self.root) for item in self.skipped],
        }


@dataclass(frozen=True, slots=True)
class PlannedMove:
    source: Path
    destination: Path
    category: str
    fingerprint: FileFingerprint
    duplicate_of: Path | None = None

    def to_dict(self, root: Path) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": _relative(self.source, root),
            "destination": _relative(self.destination, root),
            "category": self.category,
            "fingerprint": self.fingerprint.to_dict(),
        }
        if self.duplicate_of is not None:
            payload["duplicate_of"] = _relative(self.duplicate_of, root)
        return payload


@dataclass(frozen=True, slots=True)
class SkippedItem:
    path: Path
    reason: str
    detail: str | None = None

    def to_dict(self, root: Path) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": _relative(self.path, root),
            "reason": self.reason,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True, slots=True)
class OrganizationPlan:
    root: Path
    recursive: bool
    collision_strategy: str
    duplicate_strategy: str
    include_hidden: bool
    ignore_patterns: tuple[str, ...]
    rules_source: str
    scanned_files: int
    moves: tuple[PlannedMove, ...]
    skipped: tuple[SkippedItem, ...]
    duplicates: tuple[DuplicateGroup, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "organization-plan",
            "root": str(self.root),
            "options": {
                "recursive": self.recursive,
                "collision_strategy": self.collision_strategy,
                "duplicate_strategy": self.duplicate_strategy,
                "include_hidden": self.include_hidden,
                "ignore_patterns": list(self.ignore_patterns),
                "rules_source": self.rules_source,
            },
            "summary": {
                "scanned_files": self.scanned_files,
                "planned_moves": len(self.moves),
                "skipped": len(self.skipped),
                "duplicate_groups": len(self.duplicates),
                "bytes_to_move": sum(move.fingerprint.size for move in self.moves),
            },
            "operations": [move.to_dict(self.root) for move in self.moves],
            "skipped": [item.to_dict(self.root) for item in self.skipped],
            "duplicates": [group.to_dict(self.root) for group in self.duplicates],
        }


@dataclass(frozen=True, slots=True)
class UndoMove:
    current: Path
    original: Path
    fingerprint: FileFingerprint

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "current": _relative(self.current, root),
            "original": _relative(self.original, root),
            "fingerprint": self.fingerprint.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class UndoPlan:
    root: Path
    operation_id: str
    moves: tuple[UndoMove, ...]
    conflicts: tuple[str, ...]
    created_directories: tuple[Path, ...]

    @property
    def safe_to_apply(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "undo-plan",
            "root": str(self.root),
            "reverts_operation_id": self.operation_id,
            "safe_to_apply": self.safe_to_apply,
            "summary": {
                "planned_moves": len(self.moves),
                "conflicts": len(self.conflicts),
            },
            "operations": [move.to_dict(self.root) for move in self.moves],
            "conflicts": list(self.conflicts),
            "created_directories": [
                _relative(path, self.root) for path in self.created_directories
            ],
        }


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The durable result of a successfully applied organization plan."""

    root: Path
    operation_id: str
    status: str
    moved_count: int
    created_directories: tuple[Path, ...]
    manifest_path: Path
    rollback_count: int = 0

    @property
    def success(self) -> bool:
        return self.status == "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "apply-result",
            "root": str(self.root),
            "operation_id": self.operation_id,
            "status": self.status,
            "success": self.success,
            "summary": {
                "moved": self.moved_count,
                "created_directories": len(self.created_directories),
                "rolled_back": self.rollback_count,
            },
            "created_directories": [
                _relative(path, self.root) for path in self.created_directories
            ],
            "manifest": _relative(self.manifest_path, self.root),
        }


@dataclass(frozen=True, slots=True)
class UndoResult:
    """The durable result of a successfully completed undo operation."""

    root: Path
    operation_id: str
    status: str
    restored_count: int
    removed_directories: tuple[Path, ...]
    manifest_path: Path
    rollback_count: int = 0

    @property
    def success(self) -> bool:
        return self.status == "undone"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "undo-result",
            "root": str(self.root),
            "operation_id": self.operation_id,
            "status": self.status,
            "success": self.success,
            "summary": {
                "restored": self.restored_count,
                "removed_directories": len(self.removed_directories),
                "rolled_back": self.rollback_count,
            },
            "removed_directories": [
                _relative(path, self.root) for path in self.removed_directories
            ],
            "manifest": _relative(self.manifest_path, self.root),
        }
