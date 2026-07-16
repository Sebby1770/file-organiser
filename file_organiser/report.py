"""JSON/CSV reports of organize operations."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Sequence


def write_report(
    path: Path,
    moves: Sequence[tuple[Path, Path]],
    *,
    mode: str = "move",
    dry_run: bool = False,
) -> None:
    """Write a report of moves/copies to JSON or CSV based on file extension.

    Each record is (destination_or_target, source/original).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    # Moves are stored as (current/dest, original/src) for history
    ordered = [
        {
            "source": str(original),
            "destination": str(current),
            "mode": mode,
            "dry_run": dry_run,
        }
        for current, original in moves
    ]

    if suffix == ".csv":
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["source", "destination", "mode", "dry_run"]
            )
            writer.writeheader()
            writer.writerows(ordered)
    else:
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "dry_run": dry_run,
            "count": len(ordered),
            "moves": ordered,
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
