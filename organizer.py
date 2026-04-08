"""Core logic for scanning, organizing, previewing, and undoing."""
from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

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
from .rules import category_for_extension


def _scan_folder(folder: Path, rules: Dict[str, List[str]]) -> Dict[str, List[Path]]:
    """Group files in a folder by their target category.

    Only scans the top level (non-recursive). Skips hidden files and
    the history file itself.
    """
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        if entry.name.startswith("."):
            continue
        if entry.name == HISTORY_FILENAME:
            continue
        category = category_for_extension(entry.suffix, rules)
        grouped[category].append(entry)
    return grouped


def _unique_destination(dest: Path) -> Path:
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


def preview(folder: Path, rules: Dict[str, List[str]], console: Console) -> None:
    """Show what would be organized, without moving anything."""
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    grouped = _scan_folder(folder, rules)
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
        table.add_row(category, str(len(files)), examples)

    console.print(table)
    console.print(f"[bold]Total:[/bold] {total} file(s) across {len(grouped)} categor(ies)")


def organize(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    dry_run: bool = False,
) -> None:
    """Move files into category subfolders. Use dry_run to simulate."""
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    grouped = _scan_folder(folder, rules)
    if not grouped:
        console.print(f"[yellow]Nothing to organize in[/yellow] {folder}")
        return

    total = sum(len(files) for files in grouped.values())
    mode_tag = "[yellow](dry-run)[/yellow] " if dry_run else ""
    console.print(f"{mode_tag}Organizing [bold]{total}[/bold] file(s) in [cyan]{folder}[/cyan]")

    moves: List[Tuple[Path, Path]] = []
    errors: List[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Sorting files...", total=total)

        for category, files in grouped.items():
            target_dir = folder / category
            if not dry_run:
                try:
                    target_dir.mkdir(exist_ok=True)
                except OSError as e:
                    errors.append(f"Could not create {target_dir}: {e}")
                    progress.advance(task, len(files))
                    continue

            for src in files:
                dest = _unique_destination(target_dir / src.name)
                try:
                    if dry_run:
                        progress.console.log(f"[dim]would move[/dim] {src.name} -> {category}/")
                    else:
                        shutil.move(str(src), str(dest))
                        moves.append((dest, src))  # (current_location, original_location)
                except (OSError, shutil.Error) as e:
                    errors.append(f"Failed to move {src.name}: {e}")
                finally:
                    progress.advance(task, 1)

    if not dry_run and moves:
        HistoryManager(folder).save(moves)
        console.print(f"[green]✓[/green] Organized {len(moves)} file(s). Run [bold]undo[/bold] to revert.")
    elif dry_run:
        console.print("[yellow]Dry run complete.[/yellow] No files were moved.")

    if errors:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")


def undo(folder: Path, console: Console) -> None:
    """Revert the most recent organize operation in this folder."""
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    history = HistoryManager(folder)
    moves = history.load()
    if not moves:
        console.print(f"[yellow]No undo history found in[/yellow] {folder}")
        return

    console.print(f"Reverting [bold]{len(moves)}[/bold] file(s) in [cyan]{folder}[/cyan]")
    errors: List[str] = []
    restored = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Restoring files...", total=len(moves))
        for current, original in moves:
            try:
                if not current.exists():
                    errors.append(f"Missing (already moved?): {current}")
                    continue
                original.parent.mkdir(parents=True, exist_ok=True)
                final = _unique_destination(original)
                shutil.move(str(current), str(final))
                restored += 1
            except (OSError, shutil.Error) as e:
                errors.append(f"Failed to restore {current.name}: {e}")
            finally:
                progress.advance(task, 1)

    # Clean up empty category folders we created.
    for entry in folder.iterdir():
        if entry.is_dir():
            try:
                next(entry.iterdir())
            except StopIteration:
                try:
                    entry.rmdir()
                except OSError:
                    pass

    history.clear()
    console.print(f"[green]✓[/green] Restored {restored} file(s).")
    if errors:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
