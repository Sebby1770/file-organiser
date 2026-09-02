"""Versioned plans and all-or-nothing filesystem transactions.

All organizer mutations flow through this module. A plan records source and
destination fingerprints; apply validates the complete batch before touching
anything, validates again while holding a per-folder lock, and rolls the batch
back if any operation or history write fails. Overwritten destinations are
retained in a private backup area until the transaction is undone or ages out
of the history stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .history import HistoryManager
from .safety import (
    SafetyError,
    dangerous_target,
    ensure_no_symlink_components,
    is_within,
    resolved_root,
    validate_category_name,
    validate_destination_path,
    validate_internal_path,
    validate_source_path,
)

PLAN_SCHEMA = "file-organiser/plan"
PLAN_VERSION = 2
INTERNAL_DIRNAME = ".file-organiser"
LOCK_FILENAME = ".file-organiser.lock"
VALID_MODES = {"move", "copy", "symlink"}
VALID_CONFLICTS = {"rename", "skip", "overwrite"}


class PlanError(ValueError):
    """Raised when a plan is invalid, unsafe, or stale."""


class FolderBusyError(RuntimeError):
    """Raised when another mutation owns the folder lock."""


@dataclass
class PreparedOperation:
    source: Path
    destination: Path
    category: str
    source_state: Dict[str, Any]
    destination_state: Optional[Dict[str, Any]]


@dataclass
class PreparedPlan:
    root: Path
    mode: str
    on_conflict: str
    plan_id: str
    operations: List[PreparedOperation]
    legacy: bool = False


@dataclass
class TransactionResult:
    transaction_id: str
    mode: str
    planned: int
    completed: int = 0
    dry_run: bool = False
    rolled_back: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.rolled_back


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_state(path: Path, *, content_hash: bool = False) -> Dict[str, Any]:
    """Return a stable-enough precondition for a regular file."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise PlanError(f"not a regular file: {path}")
    state: Dict[str, Any] = {
        "kind": "file",
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
    }
    if content_hash:
        state["sha256"] = _sha256(path)
    return state


def entry_state(path: Path, *, content_hash: bool = False) -> Dict[str, Any]:
    """Fingerprint a regular file or symlink without following the symlink."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return {"kind": "symlink", "target": os.readlink(path)}
    return file_state(path, content_hash=content_hash)


def _state_matches(path: Path, expected: Optional[Dict[str, Any]]) -> bool:
    if expected is None:
        return not path.exists() and not path.is_symlink()
    try:
        actual = entry_state(path, content_hash="sha256" in expected)
    except OSError:
        return False
    for key in ("kind", "size", "mtime_ns", "sha256", "target"):
        if key in expected and actual.get(key) != expected.get(key):
            return False
    return True


def build_plan(
    folder: Path,
    rows: Sequence[Tuple[Path, Path, str]],
    *,
    mode: str = "move",
    on_conflict: str = "rename",
    skipped: Sequence[str] | None = None,
    profile: str | None = None,
    content_hash: bool = False,
) -> Dict[str, Any]:
    """Build a versioned, revalidatable plan from source/destination rows."""
    root = resolved_root(folder)
    if mode not in VALID_MODES:
        raise PlanError(f"unsupported plan mode: {mode}")
    if on_conflict not in VALID_CONFLICTS:
        raise PlanError(f"unsupported conflict policy: {on_conflict}")

    files: List[Dict[str, Any]] = []
    seen_sources: set[Path] = set()
    seen_destinations: set[Path] = set()
    by_category: Dict[str, int] = {}
    total_bytes = 0
    for source, destination, raw_category in rows:
        category = validate_category_name(raw_category)
        source_safe = validate_source_path(root, source)
        destination_safe = validate_destination_path(
            root, destination, allow_existing=True
        )
        if source_safe == destination_safe:
            raise PlanError(f"source and destination are identical: {source_safe}")
        if source_safe in seen_sources:
            raise PlanError(f"source appears more than once: {source_safe}")
        if destination_safe in seen_destinations:
            raise PlanError(f"destination appears more than once: {destination_safe}")
        seen_sources.add(source_safe)
        seen_destinations.add(destination_safe)

        source_fingerprint = file_state(source_safe, content_hash=content_hash)
        destination_fingerprint: Optional[Dict[str, Any]] = None
        if destination_safe.exists() or destination_safe.is_symlink():
            if on_conflict != "overwrite":
                raise PlanError(
                    f"destination unexpectedly exists for {on_conflict}: "
                    f"{destination_safe}"
                )
            destination_fingerprint = entry_state(
                destination_safe, content_hash=content_hash
            )
        total_bytes += int(source_fingerprint["size"])
        by_category[category] = by_category.get(category, 0) + 1
        files.append(
            {
                "source": str(source_safe),
                "destination": str(destination_safe),
                "category": category,
                "source_state": source_fingerprint,
                "destination_state": destination_fingerprint,
            }
        )

    overlap = seen_sources & seen_destinations
    if overlap:
        raise PlanError(
            "a path cannot be both a source and destination in one plan: "
            f"{sorted(str(path) for path in overlap)[0]}"
        )

    plan_id = uuid.uuid4().hex
    payload: Dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "version": PLAN_VERSION,
        "id": plan_id,
        "created_at": _utc_now(),
        "root": str(root),
        # Retained for callers of the original preview JSON API.
        "folder": str(root),
        "mode": mode,
        "on_conflict": on_conflict,
        "verification": "sha256" if content_hash else "size+mtime_ns",
        "count": len(files),
        "total_bytes": total_bytes,
        "by_category": by_category,
        "skipped": list(skipped or []),
        "files": files,
    }
    if profile:
        payload["profile"] = profile
    return payload


def write_plan(path: Path, plan: Dict[str, Any]) -> None:
    """Atomically write a JSON plan."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            Path(temp_name).unlink()
        except OSError:
            pass
        raise


def read_plan(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read plan {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PlanError("plan must be a JSON object")
    return raw


def _plan_path(value: object, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"plan row has no {label}")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def prepare_plan(data: Dict[str, Any]) -> PreparedPlan:
    """Validate plan schema, containment, fingerprints, and batch invariants."""
    schema = data.get("schema")
    legacy = schema is None
    if not legacy and schema != PLAN_SCHEMA:
        raise PlanError(f"unsupported plan schema: {schema!r}")
    version = int(data.get("version", 1 if legacy else 0))
    if not legacy and version != PLAN_VERSION:
        raise PlanError(
            f"unsupported plan version {version}; expected {PLAN_VERSION}"
        )
    raw_root = data.get("root", data.get("folder"))
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise PlanError("plan has no selected root")
    try:
        root = resolved_root(Path(raw_root))
    except SafetyError as exc:
        raise PlanError(str(exc)) from exc

    mode = str(data.get("mode", "move"))
    on_conflict = str(data.get("on_conflict", "rename"))
    if mode not in VALID_MODES:
        raise PlanError(f"unsupported plan mode: {mode}")
    if on_conflict not in VALID_CONFLICTS:
        raise PlanError(f"unsupported conflict policy: {on_conflict}")
    raw_files = data.get("files")
    if not isinstance(raw_files, list):
        raise PlanError("plan 'files' must be a list")
    if data.get("count") is not None and int(data["count"]) != len(raw_files):
        raise PlanError("plan count does not match its file rows")

    operations: List[PreparedOperation] = []
    seen_sources: set[Path] = set()
    seen_destinations: set[Path] = set()
    for index, row in enumerate(raw_files, 1):
        if not isinstance(row, dict):
            raise PlanError(f"plan row {index} must be an object")
        try:
            category = validate_category_name(row.get("category", "Other"))
            source = validate_source_path(
                root, _plan_path(row.get("source"), root, "source")
            )
            destination = validate_destination_path(
                root,
                _plan_path(row.get("destination"), root, "destination"),
                allow_existing=True,
            )
        except SafetyError as exc:
            raise PlanError(f"unsafe plan row {index}: {exc}") from exc
        if source == destination:
            raise PlanError(f"plan row {index} has the same source and destination")
        if source in seen_sources:
            raise PlanError(f"duplicate source in plan: {source}")
        if destination in seen_destinations:
            raise PlanError(f"duplicate destination in plan: {destination}")
        seen_sources.add(source)
        seen_destinations.add(destination)

        expected_source = row.get("source_state")
        if not legacy and not isinstance(expected_source, dict):
            raise PlanError(f"plan row {index} has no source fingerprint")
        if isinstance(expected_source, dict):
            if not _state_matches(source, expected_source):
                raise PlanError(f"source changed since plan was created: {source}")
            source_state = dict(expected_source)
        else:
            # Legacy v4 preview plans remain usable, but are immediately
            # fingerprinted and still receive every containment check.
            source_state = file_state(source)

        if not legacy and "destination_state" not in row:
            raise PlanError(f"plan row {index} has no destination precondition")
        expected_destination = row.get("destination_state")
        if expected_destination is not None and not isinstance(
            expected_destination, dict
        ):
            raise PlanError(f"invalid destination fingerprint in row {index}")
        # Legacy preview destinations were always expected to be unused.
        if not _state_matches(destination, expected_destination):
            raise PlanError(
                f"destination changed since plan was created: {destination}"
            )
        if expected_destination is not None and on_conflict != "overwrite":
            raise PlanError(
                "an existing destination requires on_conflict='overwrite': "
                f"{destination}"
            )
        operations.append(
            PreparedOperation(
                source=source,
                destination=destination,
                category=category,
                source_state=source_state,
                destination_state=(
                    dict(expected_destination)
                    if isinstance(expected_destination, dict)
                    else None
                ),
            )
        )

    overlap = seen_sources & seen_destinations
    if overlap:
        raise PlanError(
            "plan chains are not allowed; a source is also a destination: "
            f"{sorted(str(path) for path in overlap)[0]}"
        )
    return PreparedPlan(
        root=root,
        mode=mode,
        on_conflict=on_conflict,
        plan_id=str(data.get("id") or "legacy"),
        operations=operations,
        legacy=legacy,
    )


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class FolderLock:
    """Simple cross-platform, crash-recoverable per-folder mutation lock."""

    def __init__(self, root: Path) -> None:
        self.root = resolved_root(root)
        self.path = self.root / LOCK_FILENAME
        self._owned = False

    def __enter__(self) -> "FolderLock":
        payload = json.dumps({"pid": os.getpid(), "created_at": time.time()})
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._owned = True
                return self
            except FileExistsError:
                try:
                    existing = json.loads(self.path.read_text(encoding="utf-8"))
                    pid = int(existing.get("pid", 0))
                    created = float(existing.get("created_at", 0))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    # A partially written lock is not automatically stale:
                    # use its mtime so another process cannot delete a live
                    # transaction merely because it observed a short read.
                    pid = 0
                    try:
                        created = self.path.stat().st_mtime
                    except OSError:
                        created = time.time()
                stale = not _pid_is_alive(pid) and (time.time() - created) > 30
                if attempt == 0 and stale:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                raise FolderBusyError(
                    f"another file-organiser transaction is active in {self.root}"
                )
        raise FolderBusyError(f"cannot acquire transaction lock in {self.root}")

    def __exit__(self, *_args: object) -> None:
        if self._owned:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._owned = False


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Copy to a newly-created destination without silently overwriting."""
    descriptor = os.open(
        destination,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        stat.S_IMODE(source.stat().st_mode),
    )
    try:
        with source.open("rb") as src, os.fdopen(descriptor, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        shutil.copystat(source, destination, follow_symlinks=False)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            destination.unlink()
        except OSError:
            pass
        raise


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and is_within(current, stop, resolve=False):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _rollback_apply(
    mode: str,
    runtime: List[Dict[str, Any]],
    root: Path,
) -> List[str]:
    errors: List[str] = []
    for item in reversed(runtime):
        source: Path = item["source"]
        destination: Path = item["destination"]
        backup: Optional[Path] = item.get("backup")
        try:
            if item.get("applied"):
                if mode == "move":
                    if destination.exists() and not source.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(destination), str(source))
                elif destination.exists() or destination.is_symlink():
                    destination.unlink()
            if backup is not None and (backup.exists() or backup.is_symlink()):
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, destination)
        except (OSError, shutil.Error) as exc:
            errors.append(f"rollback failed for {destination}: {exc}")
    _remove_empty_parents(root / INTERNAL_DIRNAME / "backups", root)
    return errors


def execute_plan(
    data: Dict[str, Any],
    *,
    dry_run: bool = True,
    force: bool = False,
) -> TransactionResult:
    """Validate and execute a plan atomically, recording a reversible journal."""
    try:
        prepared = prepare_plan(data)
    except (PlanError, SafetyError, OSError, ValueError) as exc:
        return TransactionResult(
            transaction_id="",
            mode=str(data.get("mode", "move")),
            planned=len(data.get("files", [])) if isinstance(data.get("files"), list) else 0,
            dry_run=dry_run,
            errors=[str(exc)],
        )
    danger = dangerous_target(prepared.root)
    if danger and not force:
        return TransactionResult(
            transaction_id="",
            mode=prepared.mode,
            planned=len(prepared.operations),
            dry_run=dry_run,
            errors=[f"{danger}; pass the explicit override to continue"],
        )
    if dry_run:
        return TransactionResult(
            transaction_id=prepared.plan_id,
            mode=prepared.mode,
            planned=len(prepared.operations),
            completed=len(prepared.operations),
            dry_run=True,
        )
    if not prepared.operations:
        # A valid empty plan is a successful no-op. Do not create an undo
        # snapshot that could never restore anything.
        return TransactionResult(
            transaction_id=prepared.plan_id,
            mode=prepared.mode,
            planned=0,
            completed=0,
            dry_run=False,
        )

    transaction_id = uuid.uuid4().hex
    result = TransactionResult(
        transaction_id=transaction_id,
        mode=prepared.mode,
        planned=len(prepared.operations),
    )
    runtime: List[Dict[str, Any]] = []
    try:
        with FolderLock(prepared.root):
            # Close the scan/apply race as far as practical: validate every
            # fingerprint and symlink component again while holding the lock.
            prepared = prepare_plan(data)
            backup_dir = validate_internal_path(
                prepared.root,
                prepared.root / INTERNAL_DIRNAME / "backups" / transaction_id,
            )
            history_operations: List[Dict[str, Any]] = []
            for index, operation in enumerate(prepared.operations):
                # Revalidate immediately before each mutation too. This catches
                # external path swaps that occur after the locked preflight.
                source = validate_source_path(prepared.root, operation.source)
                destination = validate_destination_path(
                    prepared.root, operation.destination, allow_existing=True
                )
                if not _state_matches(source, operation.source_state):
                    raise PlanError(f"source changed during apply: {source}")
                if not _state_matches(destination, operation.destination_state):
                    raise PlanError(f"destination changed during apply: {destination}")

                destination.parent.mkdir(parents=True, exist_ok=True)
                validate_destination_path(
                    prepared.root, destination, allow_existing=True
                )
                item: Dict[str, Any] = {
                    "source": source,
                    "destination": destination,
                    "applied": False,
                }
                runtime.append(item)

                backup: Optional[Path] = None
                if operation.destination_state is not None:
                    if prepared.on_conflict != "overwrite":
                        raise PlanError(f"refusing to overwrite {destination}")
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    backup = validate_internal_path(
                        prepared.root,
                        backup_dir / f"{index:06d}-{destination.name}",
                    )
                    if backup.exists() or backup.is_symlink():
                        raise PlanError(f"transaction backup already exists: {backup}")
                    os.replace(destination, backup)
                    item["backup"] = backup

                if prepared.mode == "move":
                    shutil.move(str(source), str(destination))
                elif prepared.mode == "copy":
                    _copy_exclusive(source, destination)
                else:
                    os.symlink(str(source), str(destination))
                item["applied"] = True
                resulting_state = entry_state(destination)
                history_operations.append(
                    {
                        "source": str(source),
                        "destination": str(destination),
                        "category": operation.category,
                        "source_state": operation.source_state,
                        "result_state": resulting_state,
                        "backup": str(backup) if backup is not None else None,
                        "backup_state": operation.destination_state,
                    }
                )
                result.completed += 1

            moves = [
                (Path(row["destination"]), Path(row["source"]))
                for row in history_operations
            ]
            HistoryManager(prepared.root).save(
                moves,
                mode=prepared.mode,
                transaction_id=transaction_id,
                operations=history_operations,
                summary={
                    "plan_id": prepared.plan_id,
                    "count": result.completed,
                    "on_conflict": prepared.on_conflict,
                },
            )
            if not any(item.get("backup") for item in runtime):
                _remove_empty_parents(backup_dir, prepared.root)
    except BaseException as exc:
        rollback_errors = _rollback_apply(prepared.mode, runtime, prepared.root)
        result.errors.append(str(exc))
        result.errors.extend(rollback_errors)
        result.rolled_back = bool(runtime)
        result.completed = 0
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    return result


def _history_rows(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    operations = snapshot.get("operations")
    if isinstance(operations, list):
        return [dict(row) for row in operations if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    for move in snapshot.get("moves", []):
        if isinstance(move, (list, tuple)) and len(move) == 2:
            rows.append(
                {
                    "destination": str(move[0]),
                    "source": str(move[1]),
                    "result_state": None,
                    "backup": None,
                }
            )
    return rows


def _validate_undo(
    root: Path,
    snapshot: Dict[str, Any],
) -> List[Dict[str, Any]]:
    root = resolved_root(root)
    mode = str(snapshot.get("mode", "move"))
    if mode not in VALID_MODES | {"rename"}:
        raise PlanError(f"unsupported history mode: {mode}")
    rows = _history_rows(snapshot)
    if not rows:
        raise PlanError("history snapshot has no operations")
    prepared: List[Dict[str, Any]] = []
    seen_current: set[Path] = set()
    seen_original: set[Path] = set()
    for index, row in enumerate(rows, 1):
        current = _plan_path(row.get("destination"), root, "destination")
        original = _plan_path(row.get("source"), root, "source")
        # Current is a symlink only for symlink-mode history. Validate its
        # parents manually, then fingerprint the leaf without following it.
        if not is_within(current, root, resolve=False):
            raise PlanError(f"undo path is outside selected folder: {current}")
        ensure_no_symlink_components(root, current, include_leaf=False)
        if mode == "symlink":
            try:
                if not current.is_symlink():
                    raise PlanError(f"organized symlink changed or is missing: {current}")
            except OSError as exc:
                raise PlanError(f"cannot inspect organized symlink {current}: {exc}") from exc
        else:
            validate_source_path(root, current)
        if row.get("result_state") is not None and not _state_matches(
            current, row["result_state"]
        ):
            raise PlanError(f"organized file changed since apply: {current}")

        if mode in {"move", "rename"}:
            original_safe = validate_destination_path(
                root, original, allow_existing=True
            )
            if original_safe.exists() or original_safe.is_symlink():
                raise PlanError(
                    f"cannot undo because the original path is occupied: {original_safe}"
                )
        else:
            # Copy/symlink undo never modifies the still-present original, but
            # it must remain in the selected root.
            if not is_within(original, root, resolve=False):
                raise PlanError(f"original path is outside selected folder: {original}")
            original_safe = original

        backup: Optional[Path] = None
        if row.get("backup"):
            try:
                backup = validate_internal_path(root, Path(str(row["backup"])))
            except SafetyError as exc:
                raise PlanError(str(exc)) from exc
            if not backup.exists() or backup.is_symlink():
                raise PlanError(f"overwrite backup is missing or unsafe: {backup}")
            expected_backup = row.get("backup_state")
            if expected_backup is not None and not _state_matches(
                backup, expected_backup
            ):
                raise PlanError(f"overwrite backup changed: {backup}")

        if current in seen_current or original_safe in seen_original:
            raise PlanError(f"duplicate path in undo snapshot row {index}")
        seen_current.add(current)
        seen_original.add(original_safe)
        prepared.append(
            {
                "current": current,
                "original": original_safe,
                "backup": backup,
            }
        )
    return prepared


def _rollback_undo(runtime: List[Dict[str, Any]]) -> List[str]:
    errors: List[str] = []
    for item in reversed(runtime):
        current: Path = item["current"]
        original: Path = item["original"]
        backup: Optional[Path] = item.get("backup")
        staged: Optional[Path] = item.get("staged")
        try:
            if item.get("backup_restored"):
                if backup is None:
                    raise OSError("undo rollback lost its backup path")
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current, backup)
            if item.get("restored"):
                os.replace(original, current)
            elif staged is not None and (staged.exists() or staged.is_symlink()):
                os.replace(staged, current)
        except OSError as exc:
            errors.append(f"undo rollback failed for {current}: {exc}")
    return errors


def undo_latest(
    folder: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> TransactionResult:
    """Safely undo the latest journal snapshot without losing history on error."""
    try:
        root = resolved_root(folder)
    except SafetyError as exc:
        return TransactionResult("", "undo", 0, errors=[str(exc)])
    history = HistoryManager(root)
    snapshot = history.peek()
    if not snapshot:
        return TransactionResult("", "undo", 0, errors=["no undo history found"])
    rows = _history_rows(snapshot)
    transaction_id = str(snapshot.get("id") or "legacy")
    mode = str(snapshot.get("mode", "move"))
    try:
        prepared = _validate_undo(root, snapshot)
    except (PlanError, SafetyError, OSError) as exc:
        return TransactionResult(
            transaction_id, mode, len(rows), dry_run=dry_run, errors=[str(exc)]
        )
    danger = dangerous_target(root)
    if danger and not force:
        return TransactionResult(
            transaction_id,
            mode,
            len(prepared),
            dry_run=dry_run,
            errors=[f"{danger}; pass the explicit override to continue"],
        )
    if dry_run:
        return TransactionResult(
            transaction_id,
            mode,
            len(prepared),
            completed=len(prepared),
            dry_run=True,
        )

    result = TransactionResult(transaction_id, mode, len(prepared))
    runtime: List[Dict[str, Any]] = []
    undo_dir = root / INTERNAL_DIRNAME / "undo" / uuid.uuid4().hex
    try:
        with FolderLock(root):
            latest = history.peek()
            if not latest:
                raise PlanError("undo history disappeared")
            if snapshot.get("id") is not None and latest.get("id") != snapshot.get("id"):
                raise PlanError("undo history changed before apply")
            prepared = _validate_undo(root, latest)
            validate_internal_path(root, undo_dir)
            for index, operation in enumerate(prepared):
                current: Path = operation["current"]
                original: Path = operation["original"]
                backup: Optional[Path] = operation.get("backup")
                item: Dict[str, Any] = {
                    "current": current,
                    "original": original,
                    "backup": backup,
                    "restored": False,
                    "backup_restored": False,
                }
                runtime.append(item)
                if mode in {"move", "rename"}:
                    original.parent.mkdir(parents=True, exist_ok=True)
                    validate_destination_path(root, original, allow_existing=False)
                    shutil.move(str(current), str(original))
                    item["restored"] = True
                else:
                    undo_dir.mkdir(parents=True, exist_ok=True)
                    staged = validate_internal_path(
                        root, undo_dir / f"{index:06d}-{current.name}"
                    )
                    os.replace(current, staged)
                    item["staged"] = staged
                if backup is not None:
                    validate_destination_path(root, current, allow_existing=False)
                    os.replace(backup, current)
                    item["backup_restored"] = True
                result.completed += 1

            history.pop(expected_id=snapshot.get("id"))
            for item in runtime:
                staged = item.get("staged")
                if staged is not None and (staged.exists() or staged.is_symlink()):
                    staged.unlink()
            history.discard_backups(snapshot)
            _remove_empty_parents(undo_dir, root)
    except BaseException as exc:
        result.errors.append(str(exc))
        result.errors.extend(_rollback_undo(runtime))
        result.rolled_back = bool(runtime)
        result.completed = 0
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    return result
