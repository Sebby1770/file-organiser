"""Folder health check: config, history, permissions, junk, empty dirs."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from .history import HISTORY_FILENAME, HistoryManager
from .rules import discover_config, load_rules
from .scanner import iter_files


def os_writable(folder: Path) -> bool:
    probe = folder / ".file-organiser-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def doctor_folder(folder: Path, console: Console) -> int:
    """Print a desk-style diagnostic. Returns 0 if the folder looks usable."""
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]not a directory:[/red] {folder}")
        return 1

    table = Table(title=f"doctor · {folder}", header_style="bold cyan")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    writable = os_writable(folder)
    table.add_row("writable", "ok" if writable else "fail", "can create files" if writable else "permission denied")

    cfg = discover_config()
    rules = load_rules(cfg)
    table.add_row("rules", "ok", f"{len(rules)} categories · {cfg or 'built-in'}")

    hist = folder / HISTORY_FILENAME
    if hist.is_file():
        snaps = HistoryManager(folder).list_snapshots()
        table.add_row("history", "ok", f"{len(snaps)} snapshot(s) · {hist.name}")
    else:
        table.add_row("history", "—", "no undo journal yet")

    files = list(iter_files(folder, recursive=True, skip_category_folders=False))
    empty = 0
    for path in files:
        try:
            if path.is_file() and path.stat().st_size == 0:
                empty += 1
        except OSError:
            continue
    table.add_row("files", "ok", f"{len(files)} scanned, {empty} empty")

    console.print(table)
    return 0 if writable else 1
