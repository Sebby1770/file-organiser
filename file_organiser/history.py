"""Tracks file moves so they can be undone later."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

HISTORY_FILENAME = ".organizer_history.json"

# A move record is (current_path, original_path) for undo restoration.
MoveRecord = Tuple[Path, Path]


class HistoryManager:
    """Stores the most recent organize operation per target folder.

    The history is written as a JSON file inside the organized folder so
    each folder keeps its own undo record.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.history_path = folder / HISTORY_FILENAME

    def save(self, moves: List[MoveRecord], *, mode: str = "move") -> None:
        """Persist a list of (current_location, original_location) pairs.

        ``mode`` is ``move`` or ``copy`` so undo can behave correctly.
        """
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "moves": [[str(current), str(original)] for current, original in moves],
        }
        with self.history_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load(self) -> List[MoveRecord]:
        """Return moves from the last organize, or an empty list."""
        if not self.history_path.exists():
            return []
        try:
            with self.history_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        return [(Path(src), Path(dst)) for src, dst in data.get("moves", [])]

    def load_mode(self) -> str:
        """Return the operation mode (``move`` or ``copy``). Defaults to move."""
        if not self.history_path.exists():
            return "move"
        try:
            with self.history_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("mode", "move")
        except (json.JSONDecodeError, OSError):
            return "move"

    def clear(self) -> None:
        """Remove the history file if it exists."""
        if self.history_path.exists():
            try:
                self.history_path.unlink()
            except OSError:
                pass
