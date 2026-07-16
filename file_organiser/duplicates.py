"""Hash-based duplicate file detection and optional deletion."""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from rich.console import Console
from rich.table import Table

from .history import HISTORY_FILENAME
from .scanner import iter_files, matches_exclude

KeepPolicy = Literal["oldest", "newest"]


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(
    folder: Path,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    min_size: int = 0,
) -> Dict[str, List[Path]]:
    """Find duplicate files by SHA-256 content hash.

    Returns only groups with 2+ files, keyed by hash.
    """
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        min_size=min_size,
        category_names=None,
        skip_category_folders=False,
    )
    # Quick pre-group by size to avoid hashing unique sizes
    by_size: Dict[int, List[Path]] = defaultdict(list)
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        by_size[size].append(path)

    by_hash: Dict[str, List[Path]] = defaultdict(list)
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for path in paths:
            try:
                digest = file_sha256(path)
            except OSError:
                continue
            by_hash[digest].append(path)

    return {h: paths for h, paths in by_hash.items() if len(paths) >= 2}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def choose_keeper(paths: List[Path], keep: KeepPolicy = "oldest") -> Path:
    """Pick which file to keep from a duplicate group."""
    if keep == "newest":
        return max(paths, key=_mtime)
    return min(paths, key=_mtime)


def find_and_report_duplicates(
    folder: Path,
    console: Console,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    min_size: int = 0,
    delete_dupes: bool = False,
    keep: KeepPolicy = "oldest",
    dry_run: bool = False,
) -> int:
    """Find duplicates, print a table, optionally delete extras.

    Returns the number of duplicate groups found.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    console.print(f"Scanning for duplicates in [cyan]{folder}[/cyan] ...")
    groups = find_duplicates(
        folder, recursive=recursive, exclude=exclude, min_size=min_size
    )

    if not groups:
        console.print("[green]No duplicate files found.[/green]")
        return 0

    table = Table(title=f"Duplicate groups: {folder}", header_style="bold cyan")
    table.add_column("Hash (short)", style="dim")
    table.add_column("Count", justify="right", style="magenta")
    table.add_column("Files", style="white")

    total_files = 0
    for digest, paths in sorted(groups.items(), key=lambda x: -len(x[1])):
        total_files += len(paths)
        short = digest[:12]
        listing = "\n".join(str(p) for p in paths)
        table.add_row(short, str(len(paths)), listing)

    console.print(table)
    console.print(
        f"[bold]{len(groups)}[/bold] group(s), [bold]{total_files}[/bold] file(s) involved"
    )

    if not delete_dupes:
        return len(groups)

    deleted = 0
    errors: List[str] = []
    mode_tag = "[yellow](dry-run)[/yellow] " if dry_run else ""
    console.print(f"{mode_tag}Deleting duplicates (keeping {keep})...")

    for digest, paths in groups.items():
        keeper = choose_keeper(paths, keep=keep)
        for path in paths:
            if path == keeper:
                continue
            try:
                if dry_run:
                    console.print(f"  [dim]would delete[/dim] {path}")
                else:
                    path.unlink()
                    console.print(f"  [red]deleted[/red] {path}")
                deleted += 1
            except OSError as e:
                errors.append(f"{path}: {e}")

    if dry_run:
        console.print(f"[yellow]Dry run:[/yellow] would delete {deleted} file(s).")
    else:
        console.print(f"[green]✓[/green] Deleted {deleted} duplicate file(s).")
    if errors:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
    return len(groups)
