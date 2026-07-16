"""Core logic for scanning, organizing, previewing, and undoing."""
from __future__ import annotations

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

from .history import HistoryManager
from .report import write_report
from .scanner import scan_folder

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

    if report_path is not None:
        write_report(report_path, report_moves, mode=mode, dry_run=dry_run)
        if not quiet:
            console.print(f"[blue]Report written to[/blue] {report_path}")

    if errors and not quiet:
        console.print(f"[red]Encountered {len(errors)} issue(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")

    return success


def undo(folder: Path, console: Console, *, quiet: bool = False) -> int:
    """Revert the most recent organize operation in this folder.

    For copy mode, removes the copies (does not delete originals).
    For move mode, moves files back to original locations.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    history = HistoryManager(folder)
    moves = history.load()
    if not moves:
        console.print(f"[yellow]No undo history found in[/yellow] {folder}")
        return 0

    mode = history.load_mode()
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
        task = progress.add_task("Restoring files...", total=len(moves))
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

    history.clear()
    if not quiet:
        action = "Removed" if mode == "copy" else "Restored"
        console.print(f"[green]✓[/green] {action} {restored} file(s).")
    if errors and not quiet:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
    return restored


def _cleanup_empty_dirs(folder: Path) -> None:
    """Remove empty subdirectories under folder (deepest first)."""
    try:
        for dirpath, dirnames, filenames in sorted(
            ((p, [], []) for p in folder.rglob("*") if p.is_dir() and not p.is_symlink()),
            key=lambda x: len(x[0].parts),
            reverse=True,
        ):
            pass
    except OSError:
        return

    # Walk bottom-up
    dirs = sorted(
        [p for p in folder.rglob("*") if p.is_dir() and not p.is_symlink()],
        key=lambda p: len(p.parts),
        reverse=True,
    )
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
