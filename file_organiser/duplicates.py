"""Hash-based duplicate file detection and optional deletion."""
from __future__ import annotations

import hashlib
import json
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

from .scanner import HASH_CACHE_FILENAME, format_size, iter_files

KeepPolicy = Literal["oldest", "newest"]

# Stats for tests / callers that want to inspect cache effectiveness
CacheStats = Dict[str, int]


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


def send2trash_available() -> bool:
    """Return True if the optional send2trash package is importable."""
    try:
        import send2trash  # noqa: F401
        return True
    except ImportError:
        return False


def delete_path(path: Path, *, use_trash: bool = False) -> str:
    """Delete *path*, optionally via OS trash.

    Returns the action taken: ``"trashed"`` or ``"deleted"``.

    When *use_trash* is True and send2trash is installed, moves to trash.
    Otherwise permanently deletes (caller should warn when trash was requested
    but unavailable).
    """
    if use_trash and send2trash_available():
        from send2trash import send2trash

        send2trash(str(path))
        return "trashed"
    path.unlink()
    return "deleted"


class HashCache:
    """Persist path → (mtime, size, sha256) for fast re-runs of duplicate scans.

    Cache lives at ``folder / .organizer_hash_cache.json``.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / HASH_CACHE_FILENAME
        # key = absolute path string
        self._entries: Dict[str, Dict[str, object]] = {}
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                self._entries = data["entries"]
            elif isinstance(data, dict):
                # Flat format: path → record
                self._entries = {
                    k: v for k, v in data.items() if isinstance(v, dict)
                }
        except (json.JSONDecodeError, OSError):
            self._entries = {}

    def save(self) -> None:
        payload = {
            "version": 1,
            "entries": self._entries,
        }
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError:
            pass

    def get(self, path: Path) -> Optional[str]:
        """Return cached sha256 if mtime+size still match, else None."""
        key = str(path.resolve()) if path.exists() else str(path)
        try:
            st = path.stat()
            mtime = st.st_mtime
            size = st.st_size
        except OSError:
            return None
        rec = self._entries.get(key)
        if not rec:
            # Try non-resolved path key as fallback
            rec = self._entries.get(str(path))
        if not rec:
            self.misses += 1
            return None
        try:
            if float(rec["mtime"]) == float(mtime) and int(rec["size"]) == int(size):
                digest = str(rec["sha256"])
                self.hits += 1
                return digest
        except (KeyError, TypeError, ValueError):
            pass
        self.misses += 1
        return None

    def put(self, path: Path, digest: str) -> None:
        try:
            st = path.stat()
            mtime = st.st_mtime
            size = st.st_size
        except OSError:
            return
        key = str(path.resolve())
        self._entries[key] = {
            "mtime": mtime,
            "size": size,
            "sha256": digest,
        }

    def stats(self) -> CacheStats:
        return {"hits": self.hits, "misses": self.misses, "entries": len(self._entries)}


def _hash_safe(
    path: Path,
    cache: Optional[HashCache] = None,
) -> Tuple[Path, Optional[str]]:
    """Hash a file (using cache when possible); return (path, digest) or (path, None)."""
    if cache is not None:
        cached = cache.get(path)
        if cached is not None:
            return path, cached
    try:
        digest = file_sha256(path)
        if cache is not None:
            cache.put(path, digest)
        return path, digest
    except OSError:
        return path, None


def find_duplicates(
    folder: Path,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    max_depth: int | None = None,
    workers: int | None = None,
    console: Console | None = None,
    show_progress: bool = False,
    use_cache: bool = True,
    cache: Optional[HashCache] = None,
) -> Dict[str, List[Path]]:
    """Find duplicate files by SHA-256 content hash.

    Uses a thread pool for parallel hashing and an optional on-disk hash cache
    keyed by path + mtime + size. Returns only groups with 2+ files, keyed by hash.
    """
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        include=include,
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

    active_cache: Optional[HashCache] = None
    if use_cache:
        active_cache = cache if cache is not None else HashCache(folder)

    n_workers = workers if workers is not None else default_workers()
    n_workers = max(1, n_workers)

    if n_workers == 1 or len(to_hash) == 1:
        for path in to_hash:
            p, digest = _hash_safe(path, active_cache)
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
                futures = {
                    pool.submit(_hash_safe, p, active_cache): p for p in to_hash
                }
                for fut in as_completed(futures):
                    p, digest = fut.result()
                    if digest is not None:
                        by_hash[digest].append(p)
                    if progress_ctx is not None and task_id is not None:
                        progress_ctx.advance(task_id, 1)
        finally:
            if progress_ctx is not None:
                progress_ctx.stop()

    if active_cache is not None:
        active_cache.save()

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


def reclaimable_bytes(
    groups: Dict[str, List[Path]],
    *,
    keep: KeepPolicy = "oldest",
) -> Tuple[int, int]:
    """Compute reclaimable space if all but the keeper were deleted.

    Returns ``(total_reclaimable_bytes, file_count_that_could_be_removed)``.
    Size is taken from the non-keeper files (same content as keeper, so
    each extra copy's size counts fully).
    """
    total = 0
    count = 0
    for paths in groups.values():
        if len(paths) < 2:
            continue
        keeper = choose_keeper(paths, keep=keep)
        for path in paths:
            if path == keeper:
                continue
            try:
                total += path.stat().st_size
            except OSError:
                # Fall back to keeper size if dupe unreadable
                try:
                    total += keeper.stat().st_size
                except OSError:
                    pass
            count += 1
    return total, count


def find_and_report_duplicates(
    folder: Path,
    console: Console,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    max_depth: int | None = None,
    workers: int | None = None,
    delete_dupes: bool = False,
    keep: KeepPolicy = "oldest",
    dry_run: bool = False,
    use_trash: bool = False,
    use_cache: bool = True,
) -> int:
    """Find duplicates, print a table, optionally delete extras.

    When *use_trash* is True and send2trash is available, duplicates are moved
    to the OS trash instead of permanent delete. If trash was requested but
    send2trash is missing, falls back to permanent delete with a warning.

    Returns the number of duplicate groups found.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    console.print(f"Scanning for duplicates in [cyan]{folder}[/cyan] ...")
    cache = HashCache(folder) if use_cache else None
    groups = find_duplicates(
        folder,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        max_depth=max_depth,
        workers=workers,
        console=console,
        show_progress=True,
        use_cache=use_cache,
        cache=cache,
    )

    if cache is not None and (cache.hits or cache.misses):
        console.print(
            f"[dim]Hash cache: {cache.hits} hit(s), {cache.misses} miss(es)[/dim]"
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

    # Always report reclaimable space if all but keeper deleted
    reclaim, reclaim_count = reclaimable_bytes(groups, keep=keep)
    console.print(
        f"[bold]Reclaimable:[/bold] {format_size(reclaim)} "
        f"({reclaim:,} bytes) across {reclaim_count} duplicate file(s) "
        f"(keeping {keep} per group)"
    )

    if not delete_dupes:
        return len(groups)

    trash_ok = send2trash_available()
    actually_trash = bool(use_trash and trash_ok)
    if use_trash and not trash_ok:
        console.print(
            "[yellow]Warning:[/yellow] send2trash not installed — "
            "permanently deleting instead.\n"
            "  Install: [bold]pip install file-organiser[trash][/bold]"
        )

    deleted = 0
    errors: List[str] = []
    mode_tag = "[yellow](dry-run)[/yellow] " if dry_run else ""
    action_word = "trash" if actually_trash else "delete"
    console.print(f"{mode_tag}{action_word.capitalize()}ing duplicates (keeping {keep})...")

    for digest, paths in groups.items():
        keeper = choose_keeper(paths, keep=keep)
        for path in paths:
            if path == keeper:
                continue
            try:
                if dry_run:
                    verb = "would trash" if actually_trash else "would delete"
                    console.print(f"  [dim]{verb}[/dim] {path}")
                else:
                    action = delete_path(path, use_trash=actually_trash)
                    color = "yellow" if action == "trashed" else "red"
                    console.print(f"  [{color}]{action}[/{color}] {path}")
                deleted += 1
            except OSError as e:
                errors.append(f"{path}: {e}")

    if dry_run:
        verb = "trash" if actually_trash else "delete"
        console.print(f"[yellow]Dry run:[/yellow] would {verb} {deleted} file(s).")
    else:
        verb = "Trashed" if actually_trash else "Deleted"
        console.print(f"[green]✓[/green] {verb} {deleted} duplicate file(s).")
    if errors:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
    return len(groups)
