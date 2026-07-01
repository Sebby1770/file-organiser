"""SQLite run log for organizer operations."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

RUNLOG_FILENAME = ".organizer_runs.sqlite3"


class RunLog:
    """Stores historical CLI runs in an embedded SQLite database."""

    def __init__(self, folder: Path, db_path: Path | None = None) -> None:
        self.folder = folder
        self.db_path = db_path or folder / RUNLOG_FILENAME

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    command TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    planned INTEGER NOT NULL,
                    moved INTEGER NOT NULL,
                    errors INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at)")

    def record(
        self,
        command: str,
        dry_run: bool,
        planned: int,
        moved: int,
        errors: int,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.initialize()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO runs
                    (created_at, command, dry_run, planned, moved, errors, duration_seconds, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    command,
                    int(dry_run),
                    planned,
                    moved,
                    errors,
                    duration_seconds,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        self.initialize()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, created_at, command, dry_run, planned, moved, errors, duration_seconds, metadata_json
                FROM runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                **dict(row),
                "dry_run": bool(row["dry_run"]),
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]
