"""Hash-based duplicate file detection and optional deletion."""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .history import HISTORY_FILENAME
from .scanner import iter_files, matches_exclude

KeepPolicy = Literal["oldest", "newest"]


def default_workers() -> int:
    """Default thread-pool size: min(8, cpu_count)."""
    cpu = os.cpu_count() or 1
    return max(1, min(8, cpu))


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


def _hash_safe(path: Path) -> Tuple[Path, Optional[str]]:
    """Hash a file; return (path, digest) or (path, None) on error."""
    try:
        return path, file_sha256(path)
    except OSError:
        return path, None


def find_duplicates(
    folder: Path,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    min_size: int = 0,
    max_depth: int | None = None,
    workers: int | None = None,
    console: Console | None = None,
    show_progress: bool = False,
) -> Dict[str, List[Path]]:
    """Find duplicate files by SHA-256 content hash.

    Uses a thread pool for parallel hashing. Returns only groups with 2+
    files, keyed by hash.
    """
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        min_size=min_size,
        category_names=None,
        skip_category_folders=False,
        max_depth=max_depth,
    )
    # Quick pre-group by size to avoid hashing unique sizes
    by_size: Dict[int, List[Path]] = defaultdict(list)
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        by_size[size].append(path)

    to_hash: List[Path] = []
    for size, paths in by_size.items():
        if len(paths) >= 2:
            to_hash.extend(paths)

    by_hash: Dict[str, List[Path]] = defaultdict(list)
    if not to_hash:
        return {}

    n_workers = workers if workers is not None else default_workers()
    n_workers = max(1, n_workers)

    if n_workers == 1 or len(to_hash) == 1:
        for path in to_hash:
            p, digest = _hash_safe(path)
            if digest is not None:
                by_hash[digest].append(p)
    else:
        progress_ctx = None
        task_id = None
        if show_progress and console is not None:
            progress_ctx = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            )
            progress_ctx.start()
            task_id = progress_ctx.add_task("Hashing files...", total=len(to_hash))

        try:
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_hash_safe, p): p for p in to_hash}
                for fut in as_completed(futures):
                    p, digest = fut.result()
                    if digest is not None:
                        by_hash[digest].append(p)
                    if progress_ctx is not None and task_id is not None:
                        progress_ctx.advance(task_id, 1)
        finally:
            if progress_ctx is not None:
                progress_ctx.stop()

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
    max_depth: int | None = None,
    workers: int | None = None,
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
        folder,
        recursive=recursive,
        exclude=exclude,
        min_size=min_size,
        max_depth=max_depth,
        workers=workers,
        console=console,
        show_progress=True,
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
