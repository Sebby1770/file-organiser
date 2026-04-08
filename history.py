"""Tracks file moves so they can be undone later."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

HISTORY_FILENAME = ".organizer_history.json"


class HistoryManager:
    """Stores the most recent organize operation per target folder.

    The history is written as a JSON file inside the organized folder so
    each folder keeps its own undo record.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.history_path = folder / HISTORY_FILENAME

    def save(self, moves: List[Tuple[Path, Path]]) -> None:
        """Persist a list of (source, destination) moves."""
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "moves": [[str(src), str(dst)] for src, dst in moves],
        }
        with self.history_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load(self) -> List[Tuple[Path, Path]]:
        """Return moves from the last organize, or an empty list."""
        if not self.history_path.exists():
            return []
        try:
            with self.history_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        return [(Path(src), Path(dst)) for src, dst in data.get("moves", [])]

    def clear(self) -> None:
        """Remove the history file if it exists."""
        if self.history_path.exists():
            try:
                self.history_path.unlink()
            except OSError:
                pass
