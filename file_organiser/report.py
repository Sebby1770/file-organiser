"""JSON/CSV/Markdown reports of organize operations."""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Sequence


def write_report(
    path: Path,
    moves: Sequence[tuple[Path, Path]],
    *,
    mode: str = "move",
    dry_run: bool = False,
    transaction_id: str | None = None,
    errors: Sequence[str] | None = None,
) -> None:
    """Write a report of moves/copies to JSON, CSV, or Markdown by extension.

    Each record is (destination_or_target, source/original).
    Detects format by suffix: ``.csv``, ``.md`` / ``.markdown``, else JSON.
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

    report_errors = list(errors or [])
    if suffix == ".csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output, fieldnames=["source", "destination", "mode", "dry_run"]
        )
        writer.writeheader()
        writer.writerows(ordered)
        text = output.getvalue()
    elif suffix in (".md", ".markdown"):
        ts = datetime.now().isoformat(timespec="seconds")
        lines: List[str] = [
            "# File organiser report",
            "",
            f"- **Timestamp:** {ts}",
            f"- **Mode:** `{mode}`",
            f"- **Dry run:** {dry_run}",
            f"- **Count:** {len(ordered)}",
            f"- **Transaction:** `{transaction_id or 'not committed'}`",
            f"- **Status:** {'failed' if report_errors else ('planned' if dry_run else 'committed')}",
            "",
            "| Source | Destination |",
            "|--------|-------------|",
        ]
        for row in ordered:
            src = row["source"].replace("|", "\\|")
            dst = row["destination"].replace("|", "\\|")
            lines.append(f"| `{src}` | `{dst}` |")
        lines.append("")
        if report_errors:
            lines.extend(["## Errors", ""])
            lines.extend(f"- {error}" for error in report_errors)
            lines.append("")
        text = "\n".join(lines)
    else:
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "transaction_id": transaction_id,
            "status": "failed" if report_errors else ("planned" if dry_run else "committed"),
            "mode": mode,
            "dry_run": dry_run,
            "count": len(ordered),
            "errors": report_errors,
            "moves": ordered,
        }
        text = json.dumps(payload, indent=2) + "\n"

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            Path(temp_name).unlink()
        except OSError:
            pass
        raise
