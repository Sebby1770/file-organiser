"""Hash-based duplicate file detection and optional deletion."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
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

KeepPolicy = Literal["oldest", "newest", "largest"]

# Stats for tests / callers that want to inspect cache effectiveness
CacheStats = Dict[str, int]
FileIdentity = Tuple[int, int, int, int, int]


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
    """Carry out the requested removal. A failed Trash operation raises OSError."""
    if use_trash:
        try:
            from send2trash import send2trash
            send2trash(str(path))
        except Exception as exc:
            raise OSError("OS Trash is unavailable or refused this file") from exc
        return "trashed"
    path.unlink()
    return "deleted"


def _identity(path: Path) -> FileIdentity:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"No longer a regular file: {path}")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def verify_copies(paths: Sequence[Path], digest: str) -> Dict[Path, FileIdentity]:
    """Re-read the group before a destructive operation, ignoring cached hashes."""
    verified = {}
    for path in paths:
        if path.resolve(strict=True) != path:
            raise OSError(f"File path changed: {path}")
        before = _identity(path)
        if file_sha256(path) != digest or _identity(path) != before:
            raise OSError(f"File changed since duplicate scan: {path}")
        verified[path] = before
    return verified


def check_copy(path: Path, verified: Dict[Path, FileIdentity]) -> None:
    """Check for changes to a keeper or candidate since the group was re-read."""
    if path.resolve(strict=True) != path or _identity(path) != verified[path]:
        raise OSError(f"File changed since verification: {path}")


class HashCache:
    """Persist SHA-256 hashes with file identity and timestamps for repeat scans.

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
        if self.path.is_symlink() or not self.path.is_file():
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
        except (UnicodeError, json.JSONDecodeError, OSError):
            self._entries = {}

    def save(self) -> None:
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.folder,
                                             prefix=".organizer-hashes-", delete=False) as handle:
                temp_name = handle.name
                json.dump({"version": 2, "entries": self._entries}, handle)
            os.replace(temp_name, self.path)
        except OSError:
            pass  # A read-only folder can still be scanned.
        finally:
            if temp_name is not None:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def get(self, path: Path) -> Optional[str]:
        """Older cache records without an identity are ordinary misses."""
        rec = self._entries.get(str(path.absolute()))
        try:
            if isinstance(rec, dict) and rec.get("identity") == list(_identity(path)):
                digest = rec.get("sha256")
                if isinstance(digest, str) and len(digest) == 64:
                    self.hits += 1
                    return digest
        except OSError:
            pass
        self.misses += 1
        return None

    def put(self, path: Path, digest: str, identity: Optional[FileIdentity] = None) -> None:
        try:
            self._entries[str(path.absolute())] = {
                "identity": list(identity or _identity(path)), "sha256": digest,
            }
        except OSError:
            pass

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
        before = _identity(path)
        digest = file_sha256(path)
        if _identity(path) != before:
            return path, None
        if cache is not None:
            cache.put(path, digest, before)
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
    keyed by file identity, size and timestamps. Returns only groups with 2+ files, keyed by hash.
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

    return {h: sorted(paths) for h, paths in by_hash.items() if len(paths) >= 2}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def choose_keeper(paths: List[Path], keep: KeepPolicy = "oldest") -> Path:
    """Pick which file to keep from a duplicate group."""
    if keep == "newest":
        return max(paths, key=_mtime)
    if keep == "largest":
        def _size(path: Path) -> int:
            try:
                return path.stat().st_size
            except OSError:
                return 0

        return max(paths, key=_size)
    return min(paths, key=_mtime)


def _removable_copies(paths: List[Path], keeper: Path) -> Dict[Path, int]:
    """Only separate regular files can reclaim their listed bytes."""
    extras = {}
    for path in paths:
        if path == keeper:
            continue
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            extras[path] = info.st_size
    return extras


def reclaimable_bytes(
    groups: Dict[str, List[Path]],
    *,
    keep: KeepPolicy = "oldest",
) -> Tuple[int, int]:
    """Return the logical bytes and file count eligible for removal."""
    total = count = 0
    for paths in groups.values():
        if len(paths) >= 2:
            extras = _removable_copies(paths, choose_keeper(paths, keep=keep))
            total += sum(extras.values())
            count += len(extras)
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

    Trash requests fail closed if the OS Trash is unavailable.

    Returns the number of duplicate groups found.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    folder = folder.resolve()
    if delete_dupes and use_trash and not dry_run and not send2trash_available():
        raise OSError("OS Trash is unavailable. Install send2trash to move duplicates to Trash.")
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

    deleted = 0
    errors: List[str] = []
    mode_tag = "[yellow](dry-run)[/yellow] " if dry_run else ""
    action_word = "trash" if use_trash else "delete"
    console.print(f"{mode_tag}{action_word.capitalize()}ing duplicates (keeping {keep})...")

    for digest, paths in groups.items():
        keeper = choose_keeper(paths, keep=keep)
        extras = _removable_copies(paths, keeper)
        if not extras:
            continue
        try:
            verified = {} if dry_run else verify_copies(paths, digest)
        except (OSError, RuntimeError) as exc:
            errors.append(str(exc))
            continue
        for path in extras:
            try:
                if dry_run:
                    verb = "would trash" if use_trash else "would delete"
                    console.print(f"  [dim]{verb}[/dim] {path}")
                else:
                    check_copy(keeper, verified)
                    check_copy(path, verified)
                    if path.lstat().st_nlink != 1:
                        raise OSError(f"Hard-linked copy must be reviewed: {path}")
                    action = delete_path(path, use_trash=use_trash)
                    color = "yellow" if action == "trashed" else "red"
                    console.print(f"  [{color}]{action}[/{color}] {path}")
                deleted += 1
            except OSError as e:
                errors.append(f"{path}: {e}")

    if dry_run:
        verb = "trash" if use_trash else "delete"
        console.print(f"[yellow]Dry run:[/yellow] would {verb} {deleted} file(s).")
    else:
        verb = "Trashed" if use_trash else "Deleted"
        console.print(f"[green]✓[/green] {verb} {deleted} duplicate file(s).")
    if errors:
        raise OSError(f"{len(errors)} duplicate cleanup error(s). {deleted} removed. First error: {errors[0]}")
    return len(groups)
