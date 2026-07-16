"""Core logic for scanning, organizing, previewing, undoing, stats, and prune."""
from __future__ import annotations

import os
import shutil
from datetime import datetime
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

from .history import HISTORY_FILENAME, HistoryManager
from .report import write_report
from .rules import category_for_path
from .scanner import format_size, iter_files, scan_folder

ConflictStrategy = Literal["rename", "skip", "overwrite"]
DateSource = Literal["mtime", "ctime"]

MovePair = Tuple[Path, Path]  # (current/dest, original/src)


def unique_destination(dest: Path) -> Path:
    """If dest already exists, append ' (1)', ' (2)', ... until unique."""
    if not dest.exists():
        return dest
    stem, suffix, parent = dest.stem, dest.suffix, dest.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# Public alias used by tests / external callers
_unique_destination = unique_destination


def resolve_destination(
    dest: Path,
    on_conflict: ConflictStrategy = "rename",
) -> Optional[Path]:
    """Resolve dest according to conflict strategy.

    Returns None if the file should be skipped.
    """
    if not dest.exists():
        return dest
    if on_conflict == "skip":
        return None
    if on_conflict == "overwrite":
        return dest
    return unique_destination(dest)


def target_directory(
    folder: Path,
    category: str,
    src: Path,
    *,
    by_date: bool = False,
    date_source: DateSource = "mtime",
) -> Path:
    """Compute the destination directory for a file."""
    base = folder / category
    if not by_date:
        return base
    try:
        st = src.stat()
        ts = st.st_ctime if date_source == "ctime" else st.st_mtime
    except OSError:
        ts = datetime.now().timestamp()
    dt = datetime.fromtimestamp(ts)
    return base / f"{dt.year:04d}" / f"{dt.month:02d}"


def plan_moves(
    folder: Path,
    rules: Dict[str, List[str]],
    *,
    recursive: bool = False,
    exclude: Sequence[str] | None = None,
    min_size: int = 0,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    on_conflict: ConflictStrategy = "rename",
    use_mime: bool = False,
    max_depth: int | None = None,
) -> Tuple[List[Tuple[Path, Path]], List[str]]:
    """Plan (src, dest) pairs without performing I/O beyond scanning/stat.

    Returns (pairs, skip_messages).
    """
    grouped = scan_folder(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        min_size=min_size,
        use_mime=use_mime,
        max_depth=max_depth,
    )
    pairs: List[Tuple[Path, Path]] = []
    skips: List[str] = []
    # Track planned dests within this run to avoid collisions
    planned: set[Path] = set()

    for category, files in grouped.items():
        for src in files:
            dest_dir = target_directory(
                folder, category, src, by_date=by_date, date_source=date_source
            )
            dest = dest_dir / src.name
            # Avoid same-path no-ops
            try:
                if src.resolve() == dest.resolve():
                    continue
            except OSError:
                pass

            if dest in planned or dest.exists():
                if on_conflict == "skip" and (dest.exists() or dest in planned):
                    skips.append(f"skip (exists): {src.name}")
                    continue
                if on_conflict == "overwrite" and dest not in planned:
                    # allow overwrite of existing; still avoid double plan
                    pass
                else:
                    # rename until free among disk + planned
                    candidate = dest
                    if dest.exists() or dest in planned:
                        stem, suffix, parent = dest.stem, dest.suffix, dest.parent
                        counter = 1
                        while True:
                            candidate = parent / f"{stem} ({counter}){suffix}"
                            if not candidate.exists() and candidate not in planned:
                                break
                            counter += 1
                    dest = candidate

            planned.add(dest)
            pairs.append((src, dest))

    return pairs, skips


def preview(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    *,
    recursive: bool = False,
    exclude: Sequence[str] | None = None,
    min_size: int = 0,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    use_mime: bool = False,
    max_depth: int | None = None,
    quiet: bool = False,
) -> None:
    """Show what would be organized, without moving anything."""
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    grouped = scan_folder(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        min_size=min_size,
        use_mime=use_mime,
        max_depth=max_depth,
    )
    if not grouped:
        console.print(f"[yellow]No files to organize in[/yellow] {folder}")
        return

    table = Table(title=f"Preview: {folder}", header_style="bold cyan")
    table.add_column("Category", style="green")
    table.add_column("Count", justify="right", style="magenta")
    table.add_column("Example files", style="white")

    total = 0
    for category in sorted(grouped.keys()):
        files = grouped[category]
        total += len(files)
        examples = ", ".join(f.name for f in files[:3])
        if len(files) > 3:
            examples += f", ... (+{len(files) - 3} more)"
        if by_date and files:
            dest_dir = target_directory(
                folder, category, files[0], by_date=True, date_source=date_source
            )
            try:
                rel = dest_dir.relative_to(folder)
                category_label = str(rel)
            except ValueError:
                category_label = category
            table.add_row(category_label, str(len(files)), examples)
        else:
            table.add_row(category, str(len(files)), examples)

    console.print(table)
    if not quiet:
        console.print(
            f"[bold]Total:[/bold] {total} file(s) across {len(grouped)} categor(ies)"
        )


def show_stats(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    min_size: int = 0,
    use_mime: bool = False,
    max_depth: int | None = None,
    top_n: int = 10,
    quiet: bool = False,
) -> None:
    """Scan folder and print totals, category breakdown, and largest files."""
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    # Stats includes files already in category folders
    category_names = set(rules.keys()) | {"Other"}
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        min_size=min_size,
        category_names=category_names,
        skip_category_folders=False,
        max_depth=max_depth,
    )

    if not files:
        console.print(f"[yellow]No files found in[/yellow] {folder}")
        return

    # Category + size
    by_cat_count: Dict[str, int] = {}
    by_cat_bytes: Dict[str, int] = {}
    sizes: List[Tuple[Path, int]] = []
    total_bytes = 0

    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        cat = category_for_path(path, rules, use_mime=use_mime)
        by_cat_count[cat] = by_cat_count.get(cat, 0) + 1
        by_cat_bytes[cat] = by_cat_bytes.get(cat, 0) + size
        sizes.append((path, size))
        total_bytes += size

    total_files = sum(by_cat_count.values())

    console.print(f"[bold]Stats for[/bold] [cyan]{folder}[/cyan]")
    console.print(
        f"  Files: [bold]{total_files}[/bold]  "
        f"Size: [bold]{format_size(total_bytes)}[/bold] ({total_bytes:,} bytes)"
    )

    table = Table(title="By category", header_style="bold cyan")
    table.add_column("Category", style="green")
    table.add_column("Count", justify="right", style="magenta")
    table.add_column("Size", justify="right", style="white")
    table.add_column("Bytes", justify="right", style="dim")

    for cat in sorted(by_cat_count.keys(), key=lambda c: (-by_cat_bytes.get(c, 0), c)):
        table.add_row(
            cat,
            str(by_cat_count[cat]),
            format_size(by_cat_bytes[cat]),
            f"{by_cat_bytes[cat]:,}",
        )
    console.print(table)

    sizes.sort(key=lambda x: x[1], reverse=True)
    top = sizes[: max(1, top_n)]
    large = Table(title=f"Largest files (top {len(top)})", header_style="bold cyan")
    large.add_column("#", justify="right", style="dim")
    large.add_column("Size", justify="right", style="magenta")
    large.add_column("Path", style="white")
    for i, (p, sz) in enumerate(top, 1):
        try:
            rel = str(p.relative_to(folder))
        except ValueError:
            rel = str(p)
        large.add_row(str(i), format_size(sz), rel)
    console.print(large)


def prune_empty_dirs(
    folder: Path,
    console: Console | None = None,
    *,
    dry_run: bool = False,
    quiet: bool = False,
) -> int:
    """Remove empty directories under *folder* (never the root itself).

    Never deletes non-empty directories. Returns the number of dirs removed
    (or that would be removed in dry-run).
    """
    if not folder.exists() or not folder.is_dir():
        if console and not quiet:
            console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    removed = 0
    # Multiple passes: removing a leaf may empty its parent
    while True:
        dirs = sorted(
            [
                p
                for p in folder.rglob("*")
                if p.is_dir() and not p.is_symlink()
            ],
            key=lambda p: len(p.parts),
            reverse=True,
        )
        pass_removed = 0
        for d in dirs:
            if d == folder:
                continue
            # Skip hidden dirs (and anything under them already pruned by walk)
            if any(part.startswith(".") for part in d.relative_to(folder).parts):
                continue
            try:
                # Empty if no entries (or only empty after previous removals)
                entries = list(d.iterdir())
            except OSError:
                continue
            if entries:
                continue
            try:
                if dry_run:
                    if console and not quiet:
                        console.print(f"  [dim]would remove empty[/dim] {d}")
                else:
                    d.rmdir()
                    if console and not quiet:
                        console.print(f"  [dim]removed empty[/dim] {d}")
                removed += 1
                pass_removed += 1
            except OSError:
                pass
        if dry_run or pass_removed == 0:
            break

    if console and not quiet:
        if dry_run:
            console.print(
                f"[yellow]Dry run:[/yellow] would remove {removed} empty director(ies)."
            )
        else:
            console.print(
                f"[green]✓[/green] Removed {removed} empty director(ies)."
            )
    return removed


def organize(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    *,
    dry_run: bool = False,
    recursive: bool = False,
    copy: bool = False,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    min_size: int = 0,
    exclude: Sequence[str] | None = None,
    on_conflict: ConflictStrategy = "rename",
    report_path: Optional[Path] = None,
    use_mime: bool = False,
    max_depth: int | None = None,
    prune_empty: bool = False,
    quiet: bool = False,
    verbose: bool = False,
) -> int:
    """Move or copy files into category subfolders.

    Returns the number of files successfully processed.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    pairs, skips = plan_moves(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        min_size=min_size,
        by_date=by_date,
        date_source=date_source,
        on_conflict=on_conflict,
        use_mime=use_mime,
        max_depth=max_depth,
    )

    if not pairs and not skips:
        console.print(f"[yellow]Nothing to organize in[/yellow] {folder}")
        return 0

    total = len(pairs)
    mode = "copy" if copy else "move"
    mode_tag = "[yellow](dry-run)[/yellow] " if dry_run else ""
    if not quiet:
        console.print(
            f"{mode_tag}Organizing [bold]{total}[/bold] file(s) in [cyan]{folder}[/cyan] "
            f"([dim]{mode}[/dim])"
        )

    history_moves: List[MovePair] = []
    report_moves: List[MovePair] = []
    errors: List[str] = list(skips) if verbose else []
    success = 0

    progress_ctx = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
        disable=quiet,
    )

    with progress_ctx as progress:
        task = progress.add_task("Sorting files...", total=max(total, 1))

        for src, dest in pairs:
            try:
                if dry_run:
                    rel_dest = dest
                    try:
                        rel_dest = dest.relative_to(folder)
                    except ValueError:
                        pass
                    if verbose or not quiet:
                        action = "would copy" if copy else "would move"
                        progress.console.log(f"[dim]{action}[/dim] {src.name} -> {rel_dest}")
                    report_moves.append((dest, src))
                    success += 1
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if on_conflict == "overwrite" and dest.exists():
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                    if copy:
                        shutil.copy2(str(src), str(dest))
                    else:
                        shutil.move(str(src), str(dest))
                    history_moves.append((dest, src))
                    report_moves.append((dest, src))
                    success += 1
                    if verbose:
                        action = "copied" if copy else "moved"
                        progress.console.log(f"[green]{action}[/green] {src.name} -> {dest}")
            except (OSError, shutil.Error) as e:
                errors.append(f"Failed to {mode} {src.name}: {e}")
            finally:
                progress.advance(task, 1)

    if not dry_run and history_moves:
        HistoryManager(folder).save(history_moves, mode=mode)
        if not quiet:
            console.print(
                f"[green]✓[/green] Organized {success} file(s). "
                f"Run [bold]undo[/bold] to revert."
            )
    elif dry_run and not quiet:
        console.print("[yellow]Dry run complete.[/yellow] No files were modified.")

    # Prune empty dirs after move-mode organize (not copy, not dry-run)
    if prune_empty and not dry_run and not copy and history_moves:
        n = prune_empty_dirs(folder, console if not quiet else None, quiet=quiet)
        if quiet and n and console:
            pass  # stay quiet

    if report_path is not None:
        write_report(report_path, report_moves, mode=mode, dry_run=dry_run)
        if not quiet:
            console.print(f"[blue]Report written to[/blue] {report_path}")

    if errors and not quiet:
        console.print(f"[red]Encountered {len(errors)} issue(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")

    return success


def undo(
    folder: Path,
    console: Console,
    *,
    quiet: bool = False,
    list_only: bool = False,
) -> int:
    """Revert the most recent organize operation in this folder.

    For copy mode, removes the copies (does not delete originals).
    For move mode, moves files back to original locations.

    With *list_only*, prints the history stack and returns 0 without undoing.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    history = HistoryManager(folder)

    if list_only:
        snaps = history.list_snapshots()
        if not snaps:
            console.print(f"[yellow]No undo history found in[/yellow] {folder}")
            return 0
        table = Table(title=f"Undo history: {folder}", header_style="bold cyan")
        table.add_column("#", justify="right", style="dim")
        table.add_column("When", style="white")
        table.add_column("Mode", style="magenta")
        table.add_column("Files", justify="right", style="green")
        for s in snaps:
            label = "most recent" if s["index"] == 0 else str(s["index"])
            table.add_row(label, s["timestamp"], s["mode"], str(s["count"]))
        console.print(table)
        console.print(
            f"[dim]{len(snaps)} snapshot(s). Run [bold]undo[/bold] to pop the most recent.[/dim]"
        )
        return 0

    snapshot = history.pop()
    if not snapshot:
        console.print(f"[yellow]No undo history found in[/yellow] {folder}")
        return 0

    moves = [(Path(src), Path(dst)) for src, dst in snapshot.get("moves", [])]
    mode = snapshot.get("mode", "move")
    if not quiet:
        console.print(
            f"Reverting [bold]{len(moves)}[/bold] file(s) in [cyan]{folder}[/cyan] "
            f"([dim]{mode}[/dim])"
        )
    errors: List[str] = []
    restored = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        disable=quiet,
    ) as progress:
        task = progress.add_task("Restoring files...", total=max(len(moves), 1))
        for current, original in moves:
            try:
                if not current.exists():
                    errors.append(f"Missing (already moved?): {current}")
                    continue
                if mode == "copy":
                    # Undo copy: remove the organized copy; original still exists
                    current.unlink()
                    restored += 1
                else:
                    original.parent.mkdir(parents=True, exist_ok=True)
                    final = unique_destination(original)
                    shutil.move(str(current), str(final))
                    restored += 1
            except (OSError, shutil.Error) as e:
                errors.append(f"Failed to restore {current.name}: {e}")
            finally:
                progress.advance(task, 1)

    # Clean up empty directories under the folder (category / date nests)
    _cleanup_empty_dirs(folder)

    remaining = history.load_stack()
    if not quiet:
        action = "Removed" if mode == "copy" else "Restored"
        extra = (
            f" ({len(remaining)} snapshot(s) remaining)"
            if remaining
            else ""
        )
        console.print(f"[green]✓[/green] {action} {restored} file(s).{extra}")
    if errors and not quiet:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
    return restored


def _cleanup_empty_dirs(folder: Path) -> None:
    """Remove empty subdirectories under folder (deepest first)."""
    # Walk bottom-up
    try:
        dirs = sorted(
            [p for p in folder.rglob("*") if p.is_dir() and not p.is_symlink()],
            key=lambda p: len(p.parts),
            reverse=True,
        )
    except OSError:
        return
    for d in dirs:
        try:
            next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except OSError:
                pass
        except OSError:
            pass
