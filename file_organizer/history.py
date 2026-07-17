"""Durable, validated operation history and undo planning.

History files are deliberately treated as untrusted input.  A manifest can
only name canonical relative paths below its own root, and callers never act
on paths or fingerprints supplied by an :class:`~file_organizer.models.UndoPlan`.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from uuid import UUID

from .errors import HistoryError, SafetyError
from .models import FileFingerprint, SCHEMA_VERSION, UndoMove, UndoPlan
from .utils import (
    atomic_write_json,
    ensure_no_symlink_components,
    fingerprint_file,
    fingerprints_match,
    resolve_root,
    safe_join,
)

if os.name == "nt":  # pragma: no cover - exercised by the Windows CI job
    import msvcrt
else:  # pragma: no branch - exactly one locking backend exists per platform
    import fcntl

STATE_DIRECTORY_NAME = ".file-organizer"
HISTORY_DIRECTORY_NAME = "history"
LOCK_FILENAME = "lock"
MANIFEST_KIND = "file-organizer-operation"
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_OPERATIONS = 100_000

MANIFEST_STATUSES = frozenset(
    {
        "applying",
        "applied",
        "apply_rollback",
        "apply_partial",
        "rolled_back",
        "undoing",
        "undo_rollback",
        "undo_partial",
        "undone",
    }
)
MOVE_STATES = frozenset(
    {
        "pending",
        "moving",
        "moved",
        "apply_rollback",
        "rolled_back",
        "undoing",
        "restored",
        "undo_rollback",
    }
)
UNDOABLE_STATUSES = frozenset(
    {
        "applying",
        "applied",
        "apply_rollback",
        "apply_partial",
        "undoing",
        "undo_rollback",
        "undo_partial",
    }
)


class _DuplicateKeyError(ValueError):
    pass


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _source_path_key(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    return normalized.casefold() if os.name == "nt" else normalized


def _validate_operation_id(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise HistoryError("Invalid history operation ID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HistoryError("Invalid history operation ID") from exc
    if str(parsed) != value:
        raise HistoryError("History operation ID is not canonical")
    return value


def _validate_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 64:
        raise HistoryError(f"Manifest {field} must be an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise HistoryError(
            f"Manifest {field} must be an RFC 3339 UTC timestamp"
        ) from exc
    return value


def _safe_relative(root: Path, value: object, field: str) -> tuple[str, Path]:
    if not isinstance(value, str):
        raise HistoryError(f"Manifest {field} must be a relative path")
    # Backslashes become separators on Windows and colons can introduce a
    # drive or NTFS alternate-data-stream interpretation.  Manifests use one
    # portable path grammar on every platform, even when read on POSIX.
    if "\\" in value or ":" in value or "\x00" in value:
        raise HistoryError(f"Manifest {field} uses unsafe platform path syntax")
    pure = PurePosixPath(value)
    if pure.as_posix() != value:
        raise HistoryError(f"Manifest {field} is not a canonical relative path")
    try:
        path = safe_join(root, value)
    except SafetyError as exc:
        raise HistoryError(f"Unsafe manifest {field}: {exc}") from exc
    if pure.parts and pure.parts[0] == STATE_DIRECTORY_NAME:
        raise HistoryError(f"Manifest {field} targets organizer metadata")
    return value, path


def _expected_quarantine_path(
    base: str,
    operation_id: str,
    index: int,
    label: str,
) -> str:
    parent = PurePosixPath(base).parent
    directory = f".file-organizer-quarantine-{operation_id}-{index}-{label}"
    return (parent / directory / "entry").as_posix()


def _lstat_directory(path: Path, label: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise HistoryError(f"Could not inspect {label} {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise HistoryError(f"{label.capitalize()} is not a real directory: {path}")


def _ensure_directory(path: Path, label: str, *, create: bool) -> Path | None:
    try:
        _lstat_directory(path, label)
        return path
    except FileNotFoundError:
        if not create:
            return None
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise HistoryError(f"Could not create {label} {path}: {exc}") from exc
    try:
        _lstat_directory(path, label)
    except FileNotFoundError as exc:
        raise HistoryError(f"{label.capitalize()} disappeared: {path}") from exc
    return path


def _state_directory(root: Path, *, create: bool) -> Path | None:
    return _ensure_directory(
        root / STATE_DIRECTORY_NAME, "state directory", create=create
    )


def _history_directory(root: Path, *, create: bool) -> Path | None:
    state_directory = _state_directory(root, create=create)
    if state_directory is None:
        return None
    return _ensure_directory(
        state_directory / HISTORY_DIRECTORY_NAME,
        "history directory",
        create=create,
    )


def _history_exists(root: Path) -> bool:
    """Return whether history exists, validating any metadata path encountered."""

    state_directory = _state_directory(root, create=False)
    if state_directory is None:
        return False
    return _history_directory(root, create=False) is not None


@contextmanager
def _root_lock(root: Path) -> Iterator[None]:
    """Hold the advisory, per-root process lock for one complete transaction."""

    state_directory = _state_directory(root, create=True)
    assert state_directory is not None
    lock_path = state_directory / LOCK_FILENAME
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise HistoryError(f"Could not open organizer lock {lock_path}: {exc}") from exc
    locked = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise HistoryError(
                f"Organizer lock is not a private regular file: {lock_path}"
            )
        if os.name == "nt":  # pragma: no cover - exercised on Windows
            if info.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
    except OSError as exc:
        os.close(descriptor)
        raise HistoryError(f"Could not lock organizer root {root}: {exc}") from exc
    except BaseException:
        os.close(descriptor)
        raise
    try:
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":  # pragma: no cover - exercised on Windows
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _manifest_path(root: Path, operation_id: object, *, create_history: bool) -> Path:
    identifier = _validate_operation_id(operation_id)
    directory = _history_directory(root, create=create_history)
    if directory is None:
        raise HistoryError(f"No operation history found in {root}")
    return directory / f"{identifier}.json"


def _fsync_directory(path: Path) -> None:
    if sys.platform == "win32":  # Windows does not open directories this way.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_manifest(
    value: object,
    root: Path,
    *,
    expected_operation_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HistoryError("History manifest must be a JSON object")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise HistoryError("Unsupported history manifest schema version")
    if value.get("kind") != MANIFEST_KIND:
        raise HistoryError("Invalid history manifest kind")

    operation_id = _validate_operation_id(value.get("operation_id"))
    if expected_operation_id is not None and operation_id != expected_operation_id:
        raise HistoryError("History manifest ID does not match its filename")
    if value.get("root") != str(root):
        raise HistoryError("History manifest belongs to a different target root")

    status_value = value.get("status")
    if not isinstance(status_value, str) or status_value not in MANIFEST_STATUSES:
        raise HistoryError("History manifest has an invalid status")
    created_at = _validate_timestamp(value.get("created_at"), "created_at")
    updated_at = _validate_timestamp(value.get("updated_at"), "updated_at")

    raw_operations = value.get("operations")
    if not isinstance(raw_operations, list) or len(raw_operations) > MAX_OPERATIONS:
        raise HistoryError("History manifest operations must be a bounded array")
    operations: list[dict[str, Any]] = []
    source_keys: set[str] = set()
    destination_keys: set[str] = set()
    for index, raw_operation in enumerate(raw_operations):
        if not isinstance(raw_operation, dict):
            raise HistoryError(f"Manifest operation {index} must be an object")
        source, _ = _safe_relative(
            root, raw_operation.get("source"), f"operations[{index}].source"
        )
        destination, _ = _safe_relative(
            root,
            raw_operation.get("destination"),
            f"operations[{index}].destination",
        )
        source_key = _source_path_key(source)
        destination_key = _path_key(destination)
        if source_key in source_keys:
            raise HistoryError("History manifest repeats a source path")
        if destination_key in destination_keys:
            raise HistoryError("History manifest repeats a destination path")
        source_keys.add(source_key)
        destination_keys.add(destination_key)

        category = raw_operation.get("category")
        if not isinstance(category, str) or not category or len(category) > 512:
            raise HistoryError(f"Manifest operation {index} has an invalid category")
        raw_fingerprint = raw_operation.get("fingerprint")
        if not isinstance(raw_fingerprint, dict):
            raise HistoryError(f"Manifest operation {index} has an invalid fingerprint")
        if (
            type(raw_fingerprint.get("size")) is not int
            or type(raw_fingerprint.get("mtime_ns")) is not int
        ):
            raise HistoryError(f"Manifest operation {index} has an invalid fingerprint")
        try:
            fingerprint = FileFingerprint.from_dict(raw_fingerprint)
        except (TypeError, ValueError) as exc:
            raise HistoryError(
                f"Manifest operation {index} has an invalid fingerprint: {exc}"
            ) from exc
        move_state = raw_operation.get("state")
        if not isinstance(move_state, str) or move_state not in MOVE_STATES:
            raise HistoryError(f"Manifest operation {index} has an invalid state")
        quarantine_paths: dict[str, str] = {}
        for field, base, label in (
            ("source_quarantine", source, "source"),
            ("destination_quarantine", destination, "destination"),
        ):
            relative, _ = _safe_relative(
                root,
                raw_operation.get(field),
                f"operations[{index}].{field}",
            )
            expected_path = _expected_quarantine_path(base, operation_id, index, label)
            if relative != expected_path:
                raise HistoryError(
                    f"Manifest operation {index} has an invalid {field} path"
                )
            quarantine_paths[field] = relative
        operations.append(
            {
                "source": source,
                "destination": destination,
                "category": category,
                "fingerprint": fingerprint.to_dict(),
                "state": move_state,
                **quarantine_paths,
            }
        )
    source_overlap_keys = {_path_key(operation["source"]) for operation in operations}
    if source_overlap_keys & destination_keys:
        raise HistoryError(
            "History manifest contains overlapping source and destination paths"
        )

    created_directories: list[str] = []
    created_keys: set[str] = set()
    valid_parent_keys: set[str] = set()
    for operation in operations:
        parent = PurePosixPath(operation["destination"]).parent
        while parent != PurePosixPath("."):
            valid_parent_keys.add(_path_key(parent.as_posix()))
            parent = parent.parent
    raw_directories = value.get("created_directories", [])
    if not isinstance(raw_directories, list) or len(raw_directories) > MAX_OPERATIONS:
        raise HistoryError("Manifest created_directories must be a bounded array")
    for index, raw_directory in enumerate(raw_directories):
        relative, _ = _safe_relative(
            root, raw_directory, f"created_directories[{index}]"
        )
        key = _path_key(relative)
        if key in created_keys:
            raise HistoryError("History manifest repeats a created directory")
        if key not in valid_parent_keys:
            raise HistoryError(
                "History manifest records a created directory unrelated to its moves"
            )
        created_keys.add(key)
        created_directories.append(relative)

    removed_directories: list[str] = []
    removed_keys: set[str] = set()
    raw_removed = value.get("removed_directories", [])
    if not isinstance(raw_removed, list) or len(raw_removed) > len(created_directories):
        raise HistoryError("Manifest removed_directories must be a bounded array")
    for index, raw_directory in enumerate(raw_removed):
        relative, _ = _safe_relative(
            root, raw_directory, f"removed_directories[{index}]"
        )
        key = _path_key(relative)
        if key not in created_keys or key in removed_keys:
            raise HistoryError("Manifest records an invalid removed directory")
        removed_keys.add(key)
        removed_directories.append(relative)

    errors: list[str] = []
    raw_errors = value.get("errors", [])
    if not isinstance(raw_errors, list) or len(raw_errors) > 10_000:
        raise HistoryError("Manifest errors must be a bounded array")
    for raw_error in raw_errors:
        if not isinstance(raw_error, str) or len(raw_error) > 16_384:
            raise HistoryError("Manifest contains an invalid error message")
        errors.append(raw_error)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": MANIFEST_KIND,
        "operation_id": operation_id,
        "root": str(root),
        "status": status_value,
        "created_at": created_at,
        "updated_at": updated_at,
        "operations": operations,
        "created_directories": created_directories,
        "removed_directories": removed_directories,
        "errors": errors,
    }
    for timestamp_field in ("completed_at", "rolled_back_at", "undone_at"):
        if timestamp_field in value:
            manifest[timestamp_field] = _validate_timestamp(
                value[timestamp_field], timestamp_field
            )
    return manifest


def _write_manifest(root: Path, manifest: dict[str, Any]) -> Path:
    """Validate, atomically replace, and durably sync one operation journal."""

    validated = _validate_manifest(manifest, root)
    manifest.clear()
    manifest.update(validated)
    path = _manifest_path(root, manifest["operation_id"], create_history=True)
    atomic_write_json(path, manifest, private=True)
    _fsync_directory(path.parent)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    try:
        info = os.lstat(path)
    except FileNotFoundError as exc:
        raise HistoryError(f"History operation does not exist: {path.stem}") from exc
    except OSError as exc:
        raise HistoryError(f"Could not inspect history manifest {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise HistoryError(f"History manifest is not a private regular file: {path}")
    if info.st_size > MAX_MANIFEST_BYTES:
        raise HistoryError(f"History manifest is too large: {path}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise _DuplicateKeyError(f"Duplicate JSON key {key!r}")
            result[key] = item
        return result

    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise HistoryError(f"Invalid history manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HistoryError(f"History manifest must be a JSON object: {path}")
    return value


def _read_manifest_locked(
    root: Path, operation_id: object
) -> tuple[dict[str, Any], Path]:
    identifier = _validate_operation_id(operation_id)
    path = _manifest_path(root, identifier, create_history=False)
    return _validate_manifest(
        _load_json(path), root, expected_operation_id=identifier
    ), path


def _read_all_manifests_locked(root: Path) -> list[tuple[dict[str, Any], Path]]:
    directory = _history_directory(root, create=False)
    if directory is None:
        return []
    manifests: list[tuple[dict[str, Any], Path]] = []
    try:
        candidates = tuple(directory.iterdir())
    except OSError as exc:
        raise HistoryError(
            f"Could not list history directory {directory}: {exc}"
        ) from exc
    for path in candidates:
        if path.name.startswith(".") or path.suffix != ".json":
            continue
        operation_id = _validate_operation_id(path.stem)
        manifest = _validate_manifest(
            _load_json(path), root, expected_operation_id=operation_id
        )
        manifests.append((manifest, path))
    manifests.sort(
        key=lambda item: (item[0]["created_at"], item[0]["operation_id"]),
        reverse=True,
    )
    return manifests


def _select_manifest_locked(
    root: Path, operation_id: str | None
) -> tuple[dict[str, Any], Path]:
    if operation_id is not None:
        manifest, path = _read_manifest_locked(root, operation_id)
        if manifest["status"] not in UNDOABLE_STATUSES:
            if manifest["status"] == "undone":
                raise HistoryError(f"Operation {operation_id} has already been undone")
            raise HistoryError(
                f"Operation {operation_id} is not undoable (status: {manifest['status']})"
            )
        return manifest, path
    for manifest, path in _read_all_manifests_locked(root):
        if manifest["status"] in UNDOABLE_STATUSES:
            return manifest, path
    raise HistoryError(f"No undoable operation history found in {root}")


def _fingerprint_conflict(
    path: Path,
    expected: FileFingerprint,
    root: Path,
    *,
    replacement: bool,
) -> str | None:
    try:
        actual = fingerprint_file(path, root)
    except SafetyError as exc:
        return str(exc)
    if not fingerprints_match(expected, actual):
        relative = path.relative_to(root).as_posix()
        label = "Replacement file" if replacement else "File"
        return f"{label} fingerprint does not match at {relative}"
    return None


def _check_existing_parent(path: Path, root: Path) -> str | None:
    try:
        ensure_no_symlink_components(path, root, leaf=False)
    except SafetyError as exc:
        return str(exc)
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return (
                f"Parent directory is missing: {current.relative_to(root).as_posix()}"
            )
        except OSError as exc:
            return f"Could not inspect parent directory {current}: {exc}"
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return f"Unsafe parent directory: {current.relative_to(root).as_posix()}"
    return None


def _create_undo_plan_locked(
    root: Path,
    operation_id: str | None = None,
    *,
    selected: tuple[dict[str, Any], Path] | None = None,
) -> tuple[UndoPlan, dict[str, Any], Path]:
    manifest, manifest_path = selected or _select_manifest_locked(root, operation_id)
    moves: list[UndoMove] = []
    conflicts: list[str] = []

    for operation in reversed(manifest["operations"]):
        source = safe_join(root, operation["source"])
        destination = safe_join(root, operation["destination"])
        quarantines = (
            safe_join(root, operation["source_quarantine"]),
            safe_join(root, operation["destination_quarantine"]),
        )
        fingerprint = FileFingerprint.from_dict(operation["fingerprint"])
        existing_quarantines = [
            path
            for path in quarantines
            if os.path.lexists(path) or os.path.lexists(path.parent)
        ]
        if existing_quarantines:
            conflicts.append(
                "Interrupted transaction data is preserved in quarantine: "
                + ", ".join(
                    path.relative_to(root).as_posix() for path in existing_quarantines
                )
            )
            continue
        source_exists = os.path.lexists(source)
        destination_exists = os.path.lexists(destination)

        if source_exists and destination_exists:
            conflicts.append(
                "Both original and organized paths exist for "
                f"{operation['source']} -> {operation['destination']}"
            )
            continue
        if destination_exists:
            conflict = _fingerprint_conflict(
                destination, fingerprint, root, replacement=True
            )
            if conflict:
                conflicts.append(conflict)
                continue
            parent_conflict = _check_existing_parent(source, root)
            if parent_conflict:
                conflicts.append(parent_conflict)
                continue
            moves.append(
                UndoMove(
                    current=destination,
                    original=source,
                    fingerprint=fingerprint,
                )
            )
            continue
        if source_exists:
            conflict = _fingerprint_conflict(
                source, fingerprint, root, replacement=True
            )
            if conflict:
                conflicts.append(conflict)
            continue
        conflicts.append(
            "Organized file is missing from both paths: "
            f"{operation['source']} and {operation['destination']}"
        )

    created_directories = tuple(
        safe_join(root, relative) for relative in manifest["created_directories"]
    )
    plan = UndoPlan(
        root=root,
        operation_id=manifest["operation_id"],
        moves=tuple(moves),
        conflicts=tuple(conflicts),
        created_directories=created_directories,
    )
    return plan, manifest, manifest_path


def create_undo_plan(
    root: str | os.PathLike[str], operation_id: str | None = None
) -> UndoPlan:
    """Create a fingerprint-checked undo plan for one retained operation."""

    target = resolve_root(root)
    if not _history_exists(target):
        raise HistoryError(f"No operation history found in {target}")
    with _root_lock(target):
        plan, _, _ = _create_undo_plan_locked(target, operation_id)
        return plan


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    operations = [
        {
            "source": operation["source"],
            "destination": operation["destination"],
            "category": operation["category"],
            "fingerprint": dict(operation["fingerprint"]),
            "state": operation["state"],
            "source_quarantine": operation["source_quarantine"],
            "destination_quarantine": operation["destination_quarantine"],
        }
        for operation in manifest["operations"]
    ]
    result: dict[str, Any] = {
        "schema_version": manifest["schema_version"],
        "kind": manifest["kind"],
        "operation_id": manifest["operation_id"],
        "root": manifest["root"],
        "status": manifest["status"],
        "created_at": manifest["created_at"],
        "updated_at": manifest["updated_at"],
        "summary": {
            "operations": len(operations),
            "moved": sum(item["state"] == "moved" for item in operations),
            "restored": sum(item["state"] == "restored" for item in operations),
            "created_directories": len(manifest["created_directories"]),
            "errors": len(manifest["errors"]),
        },
        "operations": operations,
        "created_directories": list(manifest["created_directories"]),
        "removed_directories": list(manifest["removed_directories"]),
        "errors": list(manifest["errors"]),
    }
    for field in ("completed_at", "rolled_back_at", "undone_at"):
        if field in manifest:
            result[field] = manifest[field]
    return result


def list_history(root: str | os.PathLike[str]) -> tuple[dict[str, Any], ...]:
    """Return newest-first, fully validated operation history summaries."""

    target = resolve_root(root)
    if not _history_exists(target):
        return ()
    with _root_lock(target):
        return tuple(
            _public_manifest(manifest)
            for manifest, _ in _read_all_manifests_locked(target)
        )


__all__ = [
    "HISTORY_DIRECTORY_NAME",
    "MANIFEST_KIND",
    "STATE_DIRECTORY_NAME",
    "create_undo_plan",
    "list_history",
]
