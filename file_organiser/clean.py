"""Safe junk cleanup: empty files and common junk names."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from rich.console import Console
from rich.table import Table

from .scanner import HASH_CACHE_FILENAME, INTERNAL_FILENAMES, format_size, iter_files
from .history import HISTORY_FILENAME

# Default junk filename patterns (name-only globs)
DEFAULT_JUNK_PATTERNS: List[str] = [
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*~",
    "*.tmp",
    "*.temp",
    "ehthumbs.db",
    "Desktop.ini",
]


def is_protected_path(path: Path) -> bool:
    """Return True if *path* must never be cleaned (history, hash cache)."""
    name = path.name
    if name in INTERNAL_FILENAMES:
        return True
    if name in (HISTORY_FILENAME, HASH_CACHE_FILENAME):
        return True
    return False


def matches_junk_name(name: str, patterns: Sequence[str]) -> bool:
    """True if *name* matches any junk glob pattern (case-sensitive for exact, fnmatch)."""
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
        # Case-insensitive for well-known Windows/macOS junk
        if fnmatch.fnmatch(name.lower(), pattern.lower()):
            return True
    return False


def find_junk(
    folder: Path,
    *,
    recursive: bool = True,
    empty_files: bool = True,
    junk_patterns: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    max_depth: int | None = None,
) -> List[Tuple[Path, str, int]]:
    """Find junk candidates under *folder*.

    Returns list of ``(path, reason, size_bytes)``. Never includes history/hash cache.
    Hidden files are only considered when they match a junk pattern (e.g. ``.DS_Store``).
    """
    patterns = list(junk_patterns if junk_patterns is not None else DEFAULT_JUNK_PATTERNS)
    results: List[Tuple[Path, str, int]] = []
    seen: Set[Path] = set()

    # Standard scan skips hidden files — we need a custom walk for .DS_Store etc.
    def consider(path: Path) -> None:
        if not path.is_file() or path.is_symlink():
            return
        if is_protected_path(path):
            return
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            return

        try:
            size = path.stat().st_size
        except OSError:
            return

        reason: Optional[str] = None
        if empty_files and size == 0:
            reason = "empty (0 bytes)"
        elif matches_junk_name(path.name, patterns):
            reason = f"junk name ({path.name})"

        if reason is None:
            return

        # Honour exclude patterns
        if exclude:
            from .scanner import matches_exclude

            if matches_exclude(path, folder, exclude):
                return

        seen.add(resolved)
        results.append((path, reason, size))

    if not recursive or (max_depth is not None and max_depth <= 0):
        try:
            entries = list(folder.iterdir())
        except OSError:
            return []
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_file():
                consider(entry)
        return results

    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        current = Path(dirpath)
        try:
            dir_depth = len(current.relative_to(folder).parts)
        except ValueError:
            dir_depth = 0

        keep_dirs: List[str] = []
        for d in dirnames:
            child = current / d
            if child.is_symlink():
                continue
            # Skip hidden dirs except we still want .DS_Store *files*, not walk into .git etc.
            if d.startswith(".") and d not in (".", ".."):
                continue
            if max_depth is not None and (dir_depth + 1) > max_depth:
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for name in filenames:
            consider(current / name)

    return results


def clean_folder(
    folder: Path,
    console: Console,
    *,
    apply: bool = False,
    empty_files: bool = True,
    junk_patterns: Sequence[str] | None = None,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    max_depth: int | None = None,
    quiet: bool = False,
) -> int:
    """Scan for junk and optionally delete. Always dry-run unless *apply*.

    Returns the number of files that would be / were removed.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    candidates = find_junk(
        folder,
        recursive=recursive,
        empty_files=empty_files,
        junk_patterns=junk_patterns,
        exclude=exclude,
        max_depth=max_depth,
    )

    if not candidates:
        console.print("[green]No junk files found.[/green]")
        return 0

    table = Table(
        title=f"{'Clean (apply)' if apply else 'Clean (dry-run)'}: {folder}",
        header_style="bold cyan",
    )
    table.add_column("Path", style="white")
    table.add_column("Reason", style="yellow")
    table.add_column("Size", justify="right", style="magenta")

    total_bytes = 0
    for path, reason, size in sorted(candidates, key=lambda x: str(x[0])):
        total_bytes += size
        try:
            rel = str(path.relative_to(folder))
        except ValueError:
            rel = str(path)
        table.add_row(rel, reason, format_size(size))

    console.print(table)

    removed = 0
    errors: List[str] = []
    if apply:
        for path, _reason, _size in candidates:
            try:
                if is_protected_path(path):
                    continue
                path.unlink()
                removed += 1
            except OSError as e:
                errors.append(f"{path}: {e}")
        if not quiet:
            console.print(
                f"[green]✓[/green] Removed {removed} junk file(s) "
                f"({format_size(total_bytes)})."
            )
    else:
        removed = len(candidates)
        if not quiet:
            console.print(
                f"[yellow]Dry run:[/yellow] would remove {len(candidates)} file(s) "
                f"({format_size(total_bytes)}). Re-run with [bold]--apply[/bold] to delete."
            )

    if errors and not quiet:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")

    return removed
