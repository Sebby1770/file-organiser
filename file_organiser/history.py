"""Tracks file moves so they can be undone later.

Supports a multi-level undo stack (last N snapshots). Older single-dict
history files are loaded as a one-element stack for backward compatibility.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HISTORY_FILENAME = ".organizer_history.json"
MAX_HISTORY_STACK = 10

# A move record is (current_path, original_path) for undo restoration.
MoveRecord = Tuple[Path, Path]


def _snapshot_from_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a snapshot dict to the standard shape."""
    snapshot = {
        "timestamp": data.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
        "mode": data.get("mode", "move"),
        "moves": data.get("moves", []),
    }
    # Version 3 snapshots carry enough detail to verify and fully reverse an
    # overwrite. Preserve these optional fields while loading v1/v2 history.
    for key in ("id", "operations", "summary"):
        if key in data:
            snapshot[key] = data[key]
    return snapshot


def _parse_stack(raw: Any) -> List[Dict[str, Any]]:
    """Parse history file content into a list of snapshots (oldest → newest).

    Accepts:
      - New format: ``{"version": 3, "stack": [snapshot, ...]}``
      - Legacy list: ``[snapshot, ...]``
      - Legacy single snapshot: ``{"timestamp": ..., "mode": ..., "moves": [...]}``
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [_snapshot_from_dict(s) for s in raw if isinstance(s, dict)]
    if not isinstance(raw, dict):
        return []
    # New stack format
    if "stack" in raw and isinstance(raw["stack"], list):
        return [_snapshot_from_dict(s) for s in raw["stack"] if isinstance(s, dict)]
    # Legacy single snapshot (has moves key, no stack)
    if "moves" in raw:
        return [_snapshot_from_dict(raw)]
    return []


class HistoryManager:
    """Stores a stack of organize operations per target folder.

    The history is written as a JSON file inside the organized folder so
    each folder keeps its own undo record. Up to ``MAX_HISTORY_STACK``
    snapshots are retained (most recent last).
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.history_path = folder / HISTORY_FILENAME

    def _read_raw(self) -> Any:
        if not self.history_path.exists():
            return None
        try:
            with self.history_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_stack(self, stack: List[Dict[str, Any]]) -> None:
        """Atomically write the stack and clean backups aged out of history."""
        dropped = stack[:-MAX_HISTORY_STACK] if len(stack) > MAX_HISTORY_STACK else []
        trimmed = stack[-MAX_HISTORY_STACK:]
        payload = {
            "version": 3,
            "stack": trimmed,
        }
        self.folder.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".organizer-history-",
            suffix=".tmp",
            dir=str(self.folder),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, self.history_path)
        except BaseException:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass
            raise
        for snapshot in dropped:
            self._cleanup_snapshot_backups(snapshot)

    def load_stack(self) -> List[Dict[str, Any]]:
        """Return all snapshots oldest → newest (dicts with timestamp/mode/moves)."""
        return _parse_stack(self._read_raw())

    def save(
        self,
        moves: List[MoveRecord],
        *,
        mode: str = "move",
        transaction_id: str | None = None,
        operations: List[Dict[str, Any]] | None = None,
        summary: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Push a new snapshot onto the history stack.

        ``operations`` is optional for compatibility. Transactional callers
        use it to store fingerprints and backups for conflict-safe undo.
        """
        stack = self.load_stack()
        snapshot = {
            "id": transaction_id or uuid.uuid4().hex,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "moves": [[str(current), str(original)] for current, original in moves],
        }
        if operations is not None:
            snapshot["operations"] = operations
        if summary is not None:
            snapshot["summary"] = summary
        stack.append(snapshot)
        self._write_stack(stack)
        return snapshot

    def load(self) -> List[MoveRecord]:
        """Return moves from the most recent snapshot, or an empty list."""
        stack = self.load_stack()
        if not stack:
            return []
        data = stack[-1]
        return [(Path(src), Path(dst)) for src, dst in data.get("moves", [])]

    def load_mode(self) -> str:
        """Return the mode of the most recent snapshot. Defaults to move."""
        stack = self.load_stack()
        if not stack:
            return "move"
        return stack[-1].get("mode", "move")

    def peek(self) -> Optional[Dict[str, Any]]:
        """Return the most recent snapshot dict, or None."""
        stack = self.load_stack()
        return stack[-1] if stack else None

    def pop(self, *, expected_id: str | None = None) -> Optional[Dict[str, Any]]:
        """Remove and return the most recent snapshot; rewrite the file.

        If the stack becomes empty, the history file is deleted.
        """
        stack = self.load_stack()
        if not stack:
            return None
        if expected_id is not None and stack[-1].get("id") != expected_id:
            raise RuntimeError("undo history changed while the operation was running")
        snapshot = stack.pop()
        if stack:
            self._write_stack(stack)
        else:
            self.clear()
        return snapshot

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """Return stack summaries for display (newest first)."""
        stack = self.load_stack()
        result: List[Dict[str, Any]] = []
        for i, snap in enumerate(reversed(stack)):
            result.append(
                {
                    "index": i,  # 0 = most recent
                    "timestamp": snap.get("timestamp", "?"),
                    "mode": snap.get("mode", "move"),
                    "count": len(snap.get("moves", [])),
                }
            )
        return result

    def clear(self) -> None:
        """Remove the history file if it exists."""
        if self.history_path.exists():
            try:
                self.history_path.unlink()
            except OSError:
                pass

    def _cleanup_snapshot_backups(self, snapshot: Dict[str, Any]) -> None:
        """Remove transaction backups referenced by an aged-out snapshot.

        History is user-editable JSON, so paths are accepted only when they
        resolve below this folder's private backup directory and are not
        symlinks.
        """
        try:
            root = self.folder.resolve(strict=True)
        except OSError:
            return
        backup_root = root / ".file-organiser" / "backups"
        candidates: List[Path] = []
        for operation in snapshot.get("operations", []):
            if not isinstance(operation, dict) or not operation.get("backup"):
                continue
            candidates.append(Path(str(operation["backup"])))
        for candidate in candidates:
            try:
                resolved_parent = candidate.parent.resolve(strict=False)
                resolved_parent.relative_to(backup_root)
            except (OSError, RuntimeError, ValueError):
                continue
            try:
                if candidate.is_symlink():
                    continue
                candidate.unlink()
            except OSError:
                continue
            parent = candidate.parent
            while parent != backup_root and parent != root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    def discard_backups(self, snapshot: Dict[str, Any]) -> None:
        """Public cleanup hook used after a successful transactional undo."""
        self._cleanup_snapshot_backups(snapshot)
