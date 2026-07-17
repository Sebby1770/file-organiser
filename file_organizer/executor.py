"""Transactional plan application and undo execution."""

from __future__ import annotations

import errno
import os
import stat
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import ConflictError, HistoryError, SafetyError, TransactionError
from .history import (
    MANIFEST_KIND,
    STATE_DIRECTORY_NAME,
    _create_undo_plan_locked,
    _history_exists,
    _manifest_path,
    _root_lock,
    _select_manifest_locked,
    _write_manifest,
    create_undo_plan,
    list_history,
)
from .models import (
    ApplyResult,
    FileFingerprint,
    OrganizationPlan,
    PlannedMove,
    SCHEMA_VERSION,
    UndoPlan,
    UndoResult,
)
from .utils import (
    ensure_lexically_within,
    ensure_no_symlink_components,
    fingerprint_file,
    fingerprints_match,
    resolve_root,
    safe_join,
    utc_now,
)

COPY_CHUNK_SIZE = 1024 * 1024


class _MoveProgress:
    """Mutable receipt that survives exceptions raised after filesystem mutation."""

    __slots__ = ("destination_retained",)

    def __init__(self) -> None:
        self.destination_retained = False


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_key(path: Path) -> str:
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _source_path_key(path: Path) -> str:
    normalized = unicodedata.normalize("NFC", path.as_posix())
    return normalized.casefold() if os.name == "nt" else normalized


def _exception_text(error: BaseException) -> str:
    detail = str(error).strip() or error.__class__.__name__
    return detail[:16_384]


def _append_error(manifest: dict[str, Any], error: BaseException | str) -> str:
    detail = error if isinstance(error, str) else _exception_text(error)
    detail = detail[:16_384]
    errors = manifest.setdefault("errors", [])
    if len(errors) < 10_000:
        errors.append(detail)
    return detail


def _journal(
    root: Path,
    manifest: dict[str, Any],
    *,
    status: str | None = None,
) -> Path:
    if status is not None:
        manifest["status"] = status
    manifest["updated_at"] = utc_now()
    return _write_manifest(root, manifest)


def _try_journal(
    root: Path,
    manifest: dict[str, Any],
    journal_errors: list[str],
    *,
    status: str | None = None,
) -> Path | None:
    try:
        return _journal(root, manifest, status=status)
    except BaseException as exc:
        detail = f"Journal update failed: {_exception_text(exc)}"
        journal_errors.append(detail)
        _append_error(manifest, detail)
        return None


def _normal_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _validate_plan_path(
    path: object, root: Path, label: str
) -> tuple[Path | None, str | None]:
    try:
        candidate = Path(path)  # type: ignore[arg-type]
    except TypeError:
        return None, f"{label} is not a filesystem path"
    normalized = _normal_absolute(candidate)
    if not candidate.is_absolute() or candidate != normalized:
        return None, f"{label} must be a normalized absolute path: {candidate}"
    try:
        ensure_lexically_within(candidate, root)
    except SafetyError as exc:
        return None, str(exc)
    relative = candidate.relative_to(root)
    portable_relative = relative.as_posix()
    if (
        "\\" in portable_relative
        or ":" in portable_relative
        or "\x00" in portable_relative
    ):
        return (
            None,
            f"{label} cannot be represented safely in portable history: {candidate}",
        )
    if relative.parts and relative.parts[0] == STATE_DIRECTORY_NAME:
        return None, f"{label} targets organizer metadata: {candidate}"
    try:
        ensure_no_symlink_components(candidate, root)
    except SafetyError as exc:
        return None, str(exc)
    return candidate, None


def _check_parent_chain(path: Path, root: Path, *, allow_missing: bool) -> str | None:
    current = root
    missing = False
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            missing = True
            if allow_missing:
                continue
            return f"Parent directory is missing: {_relative(current, root)}"
        except OSError as exc:
            return f"Could not inspect parent directory {current}: {exc}"
        if missing:
            return f"Path exists below a missing parent directory: {current}"
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return f"Unsafe parent directory: {_relative(current, root)}"
    return None


def _validated_expected_fingerprint(
    fingerprint: object,
) -> tuple[FileFingerprint | None, str | None]:
    if not isinstance(fingerprint, FileFingerprint):
        return None, "planned fingerprint has an invalid type"
    try:
        validated = FileFingerprint.from_dict(fingerprint.to_dict())
    except (TypeError, ValueError) as exc:
        return None, f"planned fingerprint is invalid: {exc}"
    return validated, None


def _directories_to_create(
    moves: tuple[PlannedMove, ...], root: Path
) -> tuple[Path, ...]:
    missing: dict[str, Path] = {}
    for move in moves:
        current = move.destination.parent
        while current != root:
            if os.path.lexists(current):
                break
            missing[_path_key(current)] = current
            current = current.parent
    return tuple(
        sorted(
            missing.values(),
            key=lambda path: (len(path.relative_to(root).parts), path.as_posix()),
        )
    )


def _preflight_apply(
    plan: OrganizationPlan, root: Path
) -> tuple[tuple[PlannedMove, ...], tuple[Path, ...]]:
    """Validate every move before making any plan-related filesystem change."""

    conflicts: list[str] = []
    moves = tuple(plan.moves)
    source_keys: set[str] = set()
    destination_keys: set[str] = set()
    valid_moves: list[PlannedMove] = []

    for index, move in enumerate(moves):
        if not isinstance(move, PlannedMove):
            conflicts.append(f"Operation {index} is not a planned move")
            continue
        source, source_error = _validate_plan_path(
            move.source, root, f"Operation {index} source"
        )
        destination, destination_error = _validate_plan_path(
            move.destination, root, f"Operation {index} destination"
        )
        if source_error:
            conflicts.append(source_error)
        if destination_error:
            conflicts.append(destination_error)
        if source is None or destination is None:
            continue
        if source == destination:
            conflicts.append(
                f"Source and destination are identical: {_relative(source, root)}"
            )
            continue

        source_key = _source_path_key(source)
        destination_key = _path_key(destination)
        if source_key in source_keys:
            conflicts.append(f"Source is repeated: {_relative(source, root)}")
        if destination_key in destination_keys:
            conflicts.append(f"Destination is repeated: {_relative(destination, root)}")
        source_keys.add(source_key)
        destination_keys.add(destination_key)

        if (
            not isinstance(move.category, str)
            or not move.category
            or len(move.category) > 512
        ):
            conflicts.append(f"Operation {index} has an invalid category")
        expected, fingerprint_error = _validated_expected_fingerprint(move.fingerprint)
        if fingerprint_error:
            conflicts.append(f"{_relative(source, root)}: {fingerprint_error}")
            continue
        assert expected is not None

        parent_error = _check_parent_chain(destination, root, allow_missing=True)
        if parent_error:
            conflicts.append(parent_error)
        if not os.path.lexists(source):
            conflicts.append(f"Source does not exist: {_relative(source, root)}")
        else:
            try:
                actual = fingerprint_file(source, root)
            except SafetyError as exc:
                conflicts.append(str(exc))
            else:
                if not fingerprints_match(expected, actual):
                    conflicts.append(
                        f"Source fingerprint changed: {_relative(source, root)}"
                    )
        if os.path.lexists(destination):
            conflicts.append(
                f"Destination already exists: {_relative(destination, root)}"
            )
        valid_moves.append(move)

    source_overlap_keys = {_path_key(move.source) for move in valid_moves}
    overlap = source_overlap_keys & destination_keys
    for key in sorted(overlap):
        conflicts.append(f"A path is both a source and destination: {key}")
    if conflicts:
        raise ConflictError("Organization plan failed preflight", conflicts)

    validated_moves = tuple(valid_moves)
    directories = _directories_to_create(validated_moves, root)
    return validated_moves, directories


def _recheck_move(
    source: Path,
    destination: Path,
    expected: FileFingerprint,
    root: Path,
) -> None:
    try:
        ensure_no_symlink_components(source, root)
        ensure_no_symlink_components(destination, root)
    except SafetyError as exc:
        raise ConflictError("Filesystem changed after preflight", [str(exc)]) from exc
    parent_error = _check_parent_chain(destination, root, allow_missing=False)
    if parent_error:
        raise ConflictError("Filesystem changed after preflight", [parent_error])
    if not os.path.lexists(source):
        raise ConflictError(
            "Filesystem changed after preflight",
            [f"Source is missing: {_relative(source, root)}"],
        )
    if os.path.lexists(destination):
        raise ConflictError(
            "Filesystem changed after preflight",
            [f"Destination now exists: {_relative(destination, root)}"],
        )
    try:
        actual = fingerprint_file(source, root)
    except SafetyError as exc:
        raise ConflictError("Filesystem changed after preflight", [str(exc)]) from exc
    if not fingerprints_match(expected, actual):
        raise ConflictError(
            "Filesystem changed after preflight",
            [f"Source fingerprint changed: {_relative(source, root)}"],
        )


def _same_file_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _prepare_quarantine(quarantine: Path, root: Path) -> None:
    ensure_lexically_within(quarantine, root)
    ensure_no_symlink_components(quarantine, root, leaf=False)
    directory = quarantine.parent
    if os.path.lexists(directory):
        raise ConflictError(f"Transaction quarantine already exists: {directory}")
    os.mkdir(directory, 0o700)
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SafetyError(
            f"Transaction quarantine is not a real directory: {directory}"
        )


def _remove_quarantine_directory(quarantine: Path) -> None:
    try:
        os.rmdir(quarantine.parent)
    except FileNotFoundError:
        return


def _restore_quarantined_entry(quarantine: Path, source: Path) -> None:
    """Restore a quarantined regular entry without replacing a new source."""

    if os.path.lexists(source):
        raise ConflictError(
            "Source path was reoccupied while restoring a quarantined entry",
            [f"Preserved quarantined entry at {quarantine}"],
        )
    info = os.lstat(quarantine)
    if not stat.S_ISREG(info.st_mode):
        raise ConflictError(
            "A non-regular replacement was moved into transaction quarantine",
            [f"Preserved quarantined entry at {quarantine}"],
        )
    try:
        os.link(quarantine, source, follow_symlinks=False)
    except FileExistsError as exc:
        raise ConflictError(
            "Source path was reoccupied while restoring a quarantined entry",
            [f"Preserved quarantined entry at {quarantine}"],
        ) from exc
    restored = os.lstat(source)
    if not _same_file_identity(info, restored):
        raise ConflictError(
            "Could not verify the restored quarantined entry",
            [f"Preserved quarantined entry at {quarantine}"],
        )
    os.unlink(quarantine)
    _remove_quarantine_directory(quarantine)


def _stage_and_remove_source(
    source: Path,
    destination: Path,
    expected: FileFingerprint,
    root: Path,
    quarantine: Path,
    original_info: os.stat_result,
    progress: _MoveProgress,
) -> None:
    """Commit a move without ever unlinking a replaceable public pathname."""

    _prepare_quarantine(quarantine, root)
    try:
        os.rename(source, quarantine)
    except BaseException:
        if os.path.lexists(quarantine) or not os.path.lexists(source):
            progress.destination_retained = True
        else:
            _remove_quarantine_directory(quarantine)
        raise

    quarantined_info = os.lstat(quarantine)
    identity_matches = _same_file_identity(original_info, quarantined_info)
    fingerprint_matches = False
    if identity_matches and stat.S_ISREG(quarantined_info.st_mode):
        try:
            fingerprint_matches = fingerprints_match(
                expected, fingerprint_file(quarantine, root)
            )
        except SafetyError:
            fingerprint_matches = False

    if not identity_matches or not fingerprint_matches:
        progress.destination_retained = True
        try:
            _restore_quarantined_entry(quarantine, source)
        except BaseException as restore_error:
            raise ConflictError(
                "Source changed while it was being moved",
                [
                    f"Planned bytes are preserved at {destination}",
                    f"Concurrent entry is preserved at {quarantine}",
                    f"Automatic restore failed: {_exception_text(restore_error)}",
                ],
            ) from restore_error
        raise ConflictError(
            "Source changed while it was being moved",
            [
                f"Planned bytes are preserved at {destination}",
                f"Concurrent entry was restored to {source}",
            ],
        )

    progress.destination_retained = True
    if os.path.lexists(source):
        os.unlink(quarantine)
        _remove_quarantine_directory(quarantine)
        raise ConflictError(
            "Source path was recreated while it was being moved",
            [
                f"Moved bytes are preserved at {destination}",
                f"Concurrent entry remains at {source}",
            ],
        )

    # Only the verified entry inside our private quarantine is deleted. The
    # public source pathname can be replaced concurrently without being unlinked.
    os.unlink(quarantine)
    _remove_quarantine_directory(quarantine)


def _cleanup_redundant_quarantine(
    quarantine: Path,
    restored: Path,
    expected: FileFingerprint,
    root: Path,
) -> None:
    restored_fingerprint = fingerprint_file(restored, root)
    if not fingerprints_match(expected, restored_fingerprint):
        raise ConflictError(f"Restored file fingerprint does not match: {restored}")
    if not os.path.lexists(quarantine.parent):
        return
    if os.path.lexists(quarantine):
        quarantined_fingerprint = fingerprint_file(quarantine, root)
        if not fingerprints_match(
            expected, restored_fingerprint
        ) or not fingerprints_match(expected, quarantined_fingerprint):
            raise ConflictError(
                "Refusing to delete unresolved transaction quarantine",
                [f"Preserved quarantined entry at {quarantine}"],
            )
        try:
            os.unlink(quarantine)
        except BaseException:
            if os.path.lexists(quarantine):
                raise
    _remove_quarantine_directory(quarantine)


def _copy_across_devices(
    source: Path,
    destination: Path,
    expected: FileFingerprint,
    root: Path,
    source_quarantine: Path,
    progress: _MoveProgress,
) -> None:
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    source_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    destination_flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as exc:
        raise ConflictError(f"Could not safely open source {source}: {exc}") from exc
    destination_descriptor: int | None = None
    destination_info: os.stat_result | None = None
    try:
        source_info = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_info.st_mode):
            raise ConflictError(f"Refusing non-regular source file: {source}")
        try:
            destination_descriptor = os.open(destination, destination_flags, 0o600)
        except FileExistsError as exc:
            raise ConflictError(f"Destination already exists: {destination}") from exc
        destination_info = os.fstat(destination_descriptor)
        while True:
            chunk = os.read(source_descriptor, COPY_CHUNK_SIZE)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                view = view[written:]
        if hasattr(os, "fchmod"):
            os.fchmod(destination_descriptor, stat.S_IMODE(source_info.st_mode))
        os.fsync(destination_descriptor)
    except BaseException:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
            destination_descriptor = None
        if destination_info is not None:
            progress.destination_retained = os.path.lexists(destination)
        raise
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        os.close(source_descriptor)

    try:
        os.utime(
            destination,
            ns=(source_info.st_atime_ns, source_info.st_mtime_ns),
            follow_symlinks=False,
        )
        copied = fingerprint_file(destination, root)
        if not fingerprints_match(expected, copied):
            raise ConflictError(f"File changed during cross-device move: {source}")
        _stage_and_remove_source(
            source,
            destination,
            expected,
            root,
            source_quarantine,
            source_info,
            progress,
        )
    except BaseException:
        if destination_info is not None and os.path.lexists(destination):
            progress.destination_retained = True
        raise


def _move_no_overwrite(
    source: Path,
    destination: Path,
    expected: FileFingerprint,
    root: Path,
    source_quarantine: Path,
    progress: _MoveProgress,
) -> None:
    """Move one regular file without ever replacing a destination entry."""

    _recheck_move(source, destination, expected, root)
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise ConflictError(f"Destination already exists: {destination}") from exc
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            _copy_across_devices(
                source,
                destination,
                expected,
                root,
                source_quarantine,
                progress,
            )
            return
        raise

    destination_info = os.lstat(destination)
    try:
        if stat.S_ISLNK(destination_info.st_mode) or not stat.S_ISREG(
            destination_info.st_mode
        ):
            raise ConflictError(f"Unsafe destination created while moving {source}")
        actual = fingerprint_file(destination, root)
        if not fingerprints_match(expected, actual):
            raise ConflictError(f"Source content changed while moving {source}")
        _stage_and_remove_source(
            source,
            destination,
            expected,
            root,
            source_quarantine,
            destination_info,
            progress,
        )
    except BaseException:
        if os.path.lexists(destination):
            progress.destination_retained = True
        raise


def _create_destination_directory(path: Path, root: Path) -> None:
    ensure_no_symlink_components(path, root, leaf=False)
    parent_error = _check_parent_chain(path, root, allow_missing=False)
    if parent_error:
        raise ConflictError("Could not create destination directory", [parent_error])
    try:
        os.mkdir(path)
    except FileExistsError as exc:
        raise ConflictError(
            "Filesystem changed after preflight",
            [f"Destination directory now exists: {_relative(path, root)}"],
        ) from exc
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SafetyError(f"Created path is not a real directory: {path}")


def _quarantine_relative(
    root: Path,
    base: Path,
    operation_id: str,
    index: int,
    label: str,
) -> str:
    directory = f".file-organizer-quarantine-{operation_id}-{index}-{label}"
    return _relative(base.parent / directory / "entry", root)


def _new_manifest(
    plan: OrganizationPlan, root: Path, moves: tuple[PlannedMove, ...]
) -> dict[str, Any]:
    while True:
        operation_id = str(uuid4())
        path = _manifest_path(root, operation_id, create_history=True)
        if not os.path.lexists(path):
            break
    timestamp = utc_now()
    operations = []
    for index, move in enumerate(moves):
        operations.append(
            {
                "source": _relative(move.source, root),
                "destination": _relative(move.destination, root),
                "category": move.category,
                "fingerprint": move.fingerprint.to_dict(),
                "state": "pending",
                "source_quarantine": _quarantine_relative(
                    root, move.source, operation_id, index, "source"
                ),
                "destination_quarantine": _quarantine_relative(
                    root, move.destination, operation_id, index, "destination"
                ),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": MANIFEST_KIND,
        "operation_id": operation_id,
        "root": str(root),
        "status": "applying",
        "created_at": timestamp,
        "updated_at": timestamp,
        "operations": operations,
        "created_directories": [],
        "removed_directories": [],
        "errors": [],
    }


def _remove_recorded_directories(
    root: Path, manifest: dict[str, Any]
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    removed: list[Path] = []
    errors: list[str] = []
    already_removed = set(manifest.get("removed_directories", []))
    directories = [
        safe_join(root, relative)
        for relative in manifest.get("created_directories", [])
        if relative not in already_removed
    ]
    directories.sort(
        key=lambda path: (len(path.relative_to(root).parts), path.as_posix()),
        reverse=True,
    )
    for directory in directories:
        try:
            ensure_no_symlink_components(directory, root)
            info = os.lstat(directory)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                errors.append(f"Refused unsafe recorded directory: {directory}")
                continue
            os.rmdir(directory)
        except FileNotFoundError:
            continue
        except OSError as exc:
            if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                continue
            errors.append(f"Could not remove recorded directory {directory}: {exc}")
            continue
        relative = _relative(directory, root)
        manifest.setdefault("removed_directories", []).append(relative)
        removed.append(directory)
    return tuple(removed), tuple(errors)


def _raise_transaction_error(
    message: str,
    *,
    operation_id: str,
    manifest_path: Path,
    rollback_succeeded: bool,
    rollback_count: int,
    cause: BaseException,
) -> None:
    error = TransactionError(
        message,
        operation_id=operation_id,
        manifest_path=manifest_path,
        rollback_succeeded=rollback_succeeded,
        rollback_count=rollback_count,
    )
    raise error from cause


def _rollback_apply(
    root: Path,
    manifest: dict[str, Any],
    completed: list[int],
    original_error: BaseException,
) -> tuple[bool, int, list[str]]:
    move_errors: list[str] = []
    journal_errors: list[str] = []
    _append_error(manifest, f"Apply failed: {_exception_text(original_error)}")
    _try_journal(root, manifest, journal_errors, status="apply_rollback")
    rolled_back = 0

    for index in reversed(completed):
        operation = manifest["operations"][index]
        source = safe_join(root, operation["source"])
        destination = safe_join(root, operation["destination"])
        expected = FileFingerprint.from_dict(operation["fingerprint"])
        operation["state"] = "apply_rollback"
        _try_journal(root, manifest, journal_errors)
        progress = _MoveProgress()
        try:
            _move_no_overwrite(
                destination,
                source,
                expected,
                root,
                safe_join(root, operation["destination_quarantine"]),
                progress,
            )
        except BaseException as exc:
            if not progress.destination_retained:
                detail = (
                    f"Rollback failed for {operation['destination']} -> "
                    f"{operation['source']}: {_exception_text(exc)}"
                )
                move_errors.append(detail)
                _append_error(manifest, detail)
                continue
        try:
            _cleanup_redundant_quarantine(
                safe_join(root, operation["source_quarantine"]),
                source,
                expected,
                root,
            )
        except BaseException as exc:
            detail = (
                f"Rollback restored {operation['source']} but quarantine cleanup failed: "
                f"{_exception_text(exc)}"
            )
            move_errors.append(detail)
            _append_error(manifest, detail)
            continue
        rolled_back += 1
        operation["state"] = "rolled_back"
        _try_journal(root, manifest, journal_errors)

    completed_set = set(completed)
    for index, operation in enumerate(manifest["operations"]):
        if index not in completed_set and operation["state"] in {
            "moving",
            "apply_rollback",
        }:
            operation["state"] = "pending"

    physically_complete = not move_errors
    if physically_complete:
        manifest["rolled_back_at"] = utc_now()
        _try_journal(root, manifest, journal_errors, status="rolled_back")
        _, cleanup_errors = _remove_recorded_directories(root, manifest)
        for detail in cleanup_errors:
            _append_error(manifest, detail)
        _try_journal(root, manifest, journal_errors)
    else:
        _try_journal(root, manifest, journal_errors, status="apply_partial")
    return physically_complete, rolled_back, [*move_errors, *journal_errors]


def apply_plan(plan: OrganizationPlan) -> ApplyResult:
    """Apply an immutable plan as one locked, journalled transaction."""

    if not isinstance(plan, OrganizationPlan):
        raise TypeError("plan must be an OrganizationPlan")
    root = resolve_root(plan.root)
    if Path(plan.root) != root:
        raise SafetyError("Organization plan root is not a normalized resolved path")

    with _root_lock(root):
        moves, directories = _preflight_apply(plan, root)
        manifest = _new_manifest(plan, root, moves)
        manifest_path = _write_manifest(root, manifest)
        completed: list[int] = []
        try:
            for directory in directories:
                _create_destination_directory(directory, root)
                manifest["created_directories"].append(_relative(directory, root))
                _journal(root, manifest)

            for index, move in enumerate(moves):
                operation = manifest["operations"][index]
                operation["state"] = "moving"
                _journal(root, manifest)
                progress = _MoveProgress()
                try:
                    _move_no_overwrite(
                        move.source,
                        move.destination,
                        move.fingerprint,
                        root,
                        safe_join(root, operation["source_quarantine"]),
                        progress,
                    )
                except BaseException:
                    if progress.destination_retained:
                        completed.append(index)
                        operation["state"] = "moved"
                        journal_errors: list[str] = []
                        _try_journal(root, manifest, journal_errors)
                    raise
                completed.append(index)
                operation["state"] = "moved"
                _journal(root, manifest)

            manifest["completed_at"] = utc_now()
            manifest_path = _journal(root, manifest, status="applied")
        except BaseException as exc:
            rollback_succeeded, rollback_count, rollback_errors = _rollback_apply(
                root, manifest, completed, exc
            )
            detail = f"Apply operation {manifest['operation_id']} failed; " + (
                f"all {rollback_count} completed move(s) were rolled back"
                if rollback_succeeded
                else "rollback was incomplete and the manifest is retryable"
            )
            if rollback_errors:
                detail += f" ({'; '.join(rollback_errors)})"
            if isinstance(exc, (KeyboardInterrupt, SystemExit)) and rollback_succeeded:
                raise
            _raise_transaction_error(
                detail,
                operation_id=manifest["operation_id"],
                manifest_path=manifest_path,
                rollback_succeeded=rollback_succeeded,
                rollback_count=rollback_count,
                cause=exc,
            )

        return ApplyResult(
            root=root,
            operation_id=manifest["operation_id"],
            status="applied",
            moved_count=len(moves),
            created_directories=tuple(
                safe_join(root, relative)
                for relative in manifest["created_directories"]
            ),
            manifest_path=manifest_path,
        )


def _recheck_undo_move(
    current: Path,
    original: Path,
    expected: FileFingerprint,
    root: Path,
) -> None:
    try:
        ensure_no_symlink_components(current, root)
        ensure_no_symlink_components(original, root)
    except SafetyError as exc:
        raise ConflictError(
            "Undo filesystem changed after preflight", [str(exc)]
        ) from exc
    parent_error = _check_parent_chain(original, root, allow_missing=False)
    if parent_error:
        raise ConflictError("Undo filesystem changed after preflight", [parent_error])
    if not os.path.lexists(current):
        raise ConflictError(
            "Undo filesystem changed after preflight",
            [f"Organized file is missing: {_relative(current, root)}"],
        )
    if os.path.lexists(original):
        raise ConflictError(
            "Undo filesystem changed after preflight",
            [f"Original path now exists: {_relative(original, root)}"],
        )
    try:
        actual = fingerprint_file(current, root)
    except SafetyError as exc:
        raise ConflictError(
            "Undo filesystem changed after preflight", [str(exc)]
        ) from exc
    if not fingerprints_match(expected, actual):
        raise ConflictError(
            "Undo filesystem changed after preflight",
            [
                f"Replacement file fingerprint does not match at {_relative(current, root)}"
            ],
        )


def _rollback_undo(
    root: Path,
    manifest: dict[str, Any],
    completed: list[int],
    original_error: BaseException,
    prior_status: str,
) -> tuple[bool, int, list[str]]:
    move_errors: list[str] = []
    journal_errors: list[str] = []
    _append_error(manifest, f"Undo failed: {_exception_text(original_error)}")
    _try_journal(root, manifest, journal_errors, status="undo_rollback")
    rolled_back = 0

    for index in reversed(completed):
        operation = manifest["operations"][index]
        current = safe_join(root, operation["destination"])
        original = safe_join(root, operation["source"])
        expected = FileFingerprint.from_dict(operation["fingerprint"])
        operation["state"] = "undo_rollback"
        _try_journal(root, manifest, journal_errors)
        progress = _MoveProgress()
        try:
            _move_no_overwrite(
                original,
                current,
                expected,
                root,
                safe_join(root, operation["source_quarantine"]),
                progress,
            )
        except BaseException as exc:
            if not progress.destination_retained:
                detail = (
                    f"Undo rollback failed for {operation['source']} -> "
                    f"{operation['destination']}: {_exception_text(exc)}"
                )
                move_errors.append(detail)
                _append_error(manifest, detail)
                continue
        try:
            _cleanup_redundant_quarantine(
                safe_join(root, operation["destination_quarantine"]),
                current,
                expected,
                root,
            )
        except BaseException as exc:
            detail = (
                f"Undo rollback restored {operation['destination']} but quarantine "
                f"cleanup failed: {_exception_text(exc)}"
            )
            move_errors.append(detail)
            _append_error(manifest, detail)
            continue
        rolled_back += 1
        operation["state"] = "moved"
        _try_journal(root, manifest, journal_errors)

    completed_set = set(completed)
    for index, operation in enumerate(manifest["operations"]):
        if index not in completed_set and operation["state"] == "undoing":
            operation["state"] = "moved"

    physically_complete = not move_errors
    if physically_complete:
        fallback_status = (
            prior_status
            if prior_status in {"applied", "apply_partial", "undo_partial"}
            else "undo_partial"
        )
        _try_journal(root, manifest, journal_errors, status=fallback_status)
    else:
        _try_journal(root, manifest, journal_errors, status="undo_partial")
    return physically_complete, rolled_back, [*move_errors, *journal_errors]


def apply_undo(plan: UndoPlan) -> UndoResult:
    """Re-preflight and atomically attempt an undo from durable history."""

    if not isinstance(plan, UndoPlan):
        raise TypeError("plan must be an UndoPlan")
    root = resolve_root(plan.root)
    if Path(plan.root) != root:
        raise SafetyError("Undo plan root is not a normalized resolved path")
    if not _history_exists(root):
        raise HistoryError(f"No operation history found in {root}")

    with _root_lock(root):
        selected = _select_manifest_locked(root, plan.operation_id)
        current_plan, manifest, manifest_path = _create_undo_plan_locked(
            root, plan.operation_id, selected=selected
        )
        if current_plan.conflicts:
            raise ConflictError(
                "Undo plan failed preflight", list(current_plan.conflicts)
            )

        prior_status = manifest["status"]
        index_by_destination = {
            operation["destination"]: index
            for index, operation in enumerate(manifest["operations"])
        }
        planned_destinations = {
            _relative(move.current, root) for move in current_plan.moves
        }
        for operation in manifest["operations"]:
            operation["state"] = (
                "moved"
                if operation["destination"] in planned_destinations
                else "restored"
            )
        _journal(root, manifest, status="undoing")

        completed: list[int] = []
        try:
            for move in current_plan.moves:
                relative_destination = _relative(move.current, root)
                index = index_by_destination[relative_destination]
                operation = manifest["operations"][index]
                operation["state"] = "undoing"
                _journal(root, manifest)
                _recheck_undo_move(move.current, move.original, move.fingerprint, root)
                progress = _MoveProgress()
                try:
                    _move_no_overwrite(
                        move.current,
                        move.original,
                        move.fingerprint,
                        root,
                        safe_join(root, operation["destination_quarantine"]),
                        progress,
                    )
                except BaseException:
                    if progress.destination_retained:
                        completed.append(index)
                        operation["state"] = "restored"
                        journal_errors: list[str] = []
                        _try_journal(root, manifest, journal_errors)
                    raise
                completed.append(index)
                operation["state"] = "restored"
                _journal(root, manifest)

            manifest["undone_at"] = utc_now()
            manifest_path = _journal(root, manifest, status="undone")
        except BaseException as exc:
            rollback_succeeded, rollback_count, rollback_errors = _rollback_undo(
                root, manifest, completed, exc, prior_status
            )
            detail = f"Undo of operation {manifest['operation_id']} failed; " + (
                f"all {rollback_count} restored move(s) were rolled back"
                if rollback_succeeded
                else "rollback was incomplete and the undo remains retryable"
            )
            if rollback_errors:
                detail += f" ({'; '.join(rollback_errors)})"
            if isinstance(exc, (KeyboardInterrupt, SystemExit)) and rollback_succeeded:
                raise
            _raise_transaction_error(
                detail,
                operation_id=manifest["operation_id"],
                manifest_path=manifest_path,
                rollback_succeeded=rollback_succeeded,
                rollback_count=rollback_count,
                cause=exc,
            )

        removed_directories, cleanup_errors = _remove_recorded_directories(
            root, manifest
        )
        for detail in cleanup_errors:
            _append_error(manifest, detail)
        # File restoration is already durably committed. Directory cleanup is
        # intentionally best-effort and can never cause restored files to move.
        journal_errors: list[str] = []
        _try_journal(root, manifest, journal_errors)

        return UndoResult(
            root=root,
            operation_id=manifest["operation_id"],
            status="undone",
            restored_count=len(current_plan.moves),
            removed_directories=removed_directories,
            manifest_path=manifest_path,
        )


__all__ = [
    "apply_plan",
    "apply_undo",
    "create_undo_plan",
    "list_history",
]
