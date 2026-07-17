"""Core logic for scanning, organizing, previewing, undoing, stats, and prune."""
from __future__ import annotations

import json
import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

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
from rich.tree import Tree as RichTree

from .history import HISTORY_FILENAME, HistoryManager
from .report import write_report
from .rules import category_for_path
from .scanner import format_size, iter_files, matches_include, scan_folder

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
    include: Sequence[str] | None = None,
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
        include=include,
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


def build_preview_plan(
    folder: Path,
    rules: Dict[str, List[str]],
    *,
    recursive: bool = False,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    use_mime: bool = False,
    max_depth: int | None = None,
    on_conflict: ConflictStrategy = "rename",
) -> Dict[str, Any]:
    """Build a machine-readable organize plan.

    Schema::

        {
          "folder": "...",
          "count": N,
          "files": [
            {"source": "...", "destination": "...", "category": "..."}
          ]
        }
    """
    pairs, _skips = plan_moves(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        by_date=by_date,
        date_source=date_source,
        on_conflict=on_conflict,
        use_mime=use_mime,
        max_depth=max_depth,
    )
    files_out: List[Dict[str, str]] = []
    for src, dest in pairs:
        cat = category_for_path(src, rules, use_mime=use_mime)
        files_out.append(
            {
                "source": str(src),
                "destination": str(dest),
                "category": cat,
            }
        )
    return {
        "folder": str(folder),
        "count": len(files_out),
        "files": files_out,
    }


def preview(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    *,
    recursive: bool = False,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    use_mime: bool = False,
    max_depth: int | None = None,
    quiet: bool = False,
    as_json: bool = False,
) -> None:
    """Show what would be organized, without moving anything.

    When *as_json* is True, print a machine-readable plan to stdout.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    if as_json:
        plan = build_preview_plan(
            folder,
            rules,
            recursive=recursive,
            exclude=exclude,
            include=include,
            min_size=min_size,
            by_date=by_date,
            date_source=date_source,
            use_mime=use_mime,
            max_depth=max_depth,
        )
        # Print raw JSON to stdout (no rich styling) for machine consumers
        print(json.dumps(plan, indent=2))
        return

    grouped = scan_folder(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        include=include,
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
    include: Sequence[str] | None = None,
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
        include=include,
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
    symlink: bool = False,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    min_size: int = 0,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    on_conflict: ConflictStrategy = "rename",
    report_path: Optional[Path] = None,
    use_mime: bool = False,
    max_depth: int | None = None,
    prune_empty: bool = False,
    quiet: bool = False,
    verbose: bool = False,
) -> int:
    """Move, copy, or symlink files into category subfolders.

    *symlink* creates a symlink at the destination pointing at the source
    (sources stay in place). Mutually preferred over copy when both set.

    Returns the number of files successfully processed.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    if symlink and copy:
        # Symlink takes precedence; warn via quiet path only if verbose
        if verbose:
            console.print("[yellow]Both --symlink and --copy set; using symlink.[/yellow]")

    pairs, skips = plan_moves(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        include=include,
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
    if symlink:
        mode = "symlink"
    elif copy:
        mode = "copy"
    else:
        mode = "move"
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
                        if symlink:
                            action = "would symlink"
                        elif copy:
                            action = "would copy"
                        else:
                            action = "would move"
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
                    if symlink:
                        # Absolute target so the link works from category folders
                        target = src.resolve()
                        os.symlink(target, dest)
                    elif copy:
                        shutil.copy2(str(src), str(dest))
                    else:
                        shutil.move(str(src), str(dest))
                    history_moves.append((dest, src))
                    report_moves.append((dest, src))
                    success += 1
                    if verbose:
                        if symlink:
                            action = "symlinked"
                        elif copy:
                            action = "copied"
                        else:
                            action = "moved"
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

    # Prune empty dirs after move-mode organize (not copy/symlink, not dry-run)
    if prune_empty and not dry_run and mode == "move" and history_moves:
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

    For copy/symlink mode, removes the copies/links (does not delete originals).
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
                # Symlinks: is_file/exists may follow; check is_symlink too
                if not current.exists() and not current.is_symlink():
                    errors.append(f"Missing (already moved?): {current}")
                    continue
                if mode in ("copy", "symlink"):
                    # Undo copy/symlink: remove the organized entry; original stays
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
        action = "Removed" if mode in ("copy", "symlink") else "Restored"
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


def find_files(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    *,
    category: Optional[str] = None,
    ext: Optional[str] = None,
    name: Optional[str] = None,
    recursive: bool = False,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    max_depth: int | None = None,
    use_mime: bool = False,
    quiet: bool = False,
) -> int:
    """Find files matching category / extension / name filters.

    Returns the number of matches.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    category_names = set(rules.keys()) | {"Other"}
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        category_names=category_names,
        skip_category_folders=False,
        max_depth=max_depth,
    )

    # Normalize extension filter
    ext_norm: Optional[str] = None
    if ext:
        ext_norm = ext if ext.startswith(".") else f".{ext}"
        ext_norm = ext_norm.lower()

    name_patterns = [name] if name else []

    matches: List[Tuple[Path, str, int]] = []
    for path in files:
        cat = category_for_path(path, rules, use_mime=use_mime)
        if category and cat.lower() != category.lower():
            continue
        if ext_norm and path.suffix.lower() != ext_norm:
            continue
        if name_patterns and not matches_include(path, folder, name_patterns):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        matches.append((path, cat, size))

    if not matches:
        console.print(f"[yellow]No matching files in[/yellow] {folder}")
        return 0

    table = Table(title=f"Find: {folder}", header_style="bold cyan")
    table.add_column("Path", style="white")
    table.add_column("Category", style="green")
    table.add_column("Size", justify="right", style="magenta")
    table.add_column("Bytes", justify="right", style="dim")

    total_bytes = 0
    for path, cat, size in sorted(matches, key=lambda x: str(x[0])):
        total_bytes += size
        try:
            rel = str(path.relative_to(folder))
        except ValueError:
            rel = str(path)
        table.add_row(rel, cat, format_size(size), f"{size:,}")

    console.print(table)
    if not quiet:
        console.print(
            f"[bold]{len(matches)}[/bold] file(s), "
            f"[bold]{format_size(total_bytes)}[/bold] total"
        )
    return len(matches)


def show_tree(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    max_depth: int | None = None,
    use_mime: bool = False,
    quiet: bool = False,
) -> None:
    """Show a category-folder tree of the current layout with counts and sizes."""
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    category_names = set(rules.keys()) | {"Other"}
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        category_names=category_names,
        skip_category_folders=False,
        max_depth=max_depth,
    )

    # Group by category; also note loose (root-level) files
    by_cat: Dict[str, List[Tuple[Path, int]]] = defaultdict(list)
    total_bytes = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        cat = category_for_path(path, rules, use_mime=use_mime)
        by_cat[cat].append((path, size))
        total_bytes += size

    tree = RichTree(
        f"[bold cyan]{folder.name}[/bold cyan] "
        f"[dim]({sum(len(v) for v in by_cat.values())} files, "
        f"{format_size(total_bytes)})[/dim]"
    )

    if not by_cat:
        tree.add("[dim](empty)[/dim]")
        console.print(tree)
        return

    for cat in sorted(by_cat.keys()):
        items = by_cat[cat]
        cat_bytes = sum(s for _, s in items)
        label = (
            f"[green]{cat}/[/green] "
            f"[magenta]{len(items)}[/magenta] "
            f"[dim]{format_size(cat_bytes)}[/dim]"
        )
        node = tree.add(label)

        # Show date subdirs if present under category folder
        subdirs: Dict[str, List[Tuple[Path, int]]] = defaultdict(list)
        direct: List[Tuple[Path, int]] = []
        for path, size in items:
            try:
                rel = path.relative_to(folder)
            except ValueError:
                node.add(f"[dim]{path.name}[/dim] {format_size(size)}")
                continue
            if len(rel.parts) >= 3 and rel.parts[0] == cat:
                # Category/YYYY/MM/... or Category/sub/...
                sub = "/".join(rel.parts[1:-1])
                if sub:
                    subdirs[sub].append((path, size))
                else:
                    direct.append((path, size))
            elif len(rel.parts) == 2 and rel.parts[0] == cat:
                direct.append((path, size))
            else:
                # Loose file not under category dir
                node.add(f"[dim]{rel.as_posix()}[/dim] {format_size(size)}")

        for sub in sorted(subdirs.keys()):
            sub_items = subdirs[sub]
            sub_bytes = sum(s for _, s in sub_items)
            node.add(
                f"[cyan]{sub}/[/cyan] "
                f"[magenta]{len(sub_items)}[/magenta] "
                f"[dim]{format_size(sub_bytes)}[/dim]"
            )

        # Compact sample names for files directly under the category
        if direct and not subdirs:
            samples = ", ".join(p.name for p, _ in direct[:5])
            if len(direct) > 5:
                samples += f", … (+{len(direct) - 5})"
            if samples:
                node.add(f"[dim]{samples}[/dim]")

    console.print(tree)
    if not quiet:
        console.print(
            f"[bold]{sum(len(v) for v in by_cat.values())}[/bold] file(s) in "
            f"[bold]{len(by_cat)}[/bold] categor(ies), "
            f"[bold]{format_size(total_bytes)}[/bold]"
        )


def show_extensions(
    folder: Path,
    console: Console,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    max_depth: int | None = None,
    quiet: bool = False,
) -> int:
    """Table of every extension with count and total bytes, sorted by size.

    Returns the number of distinct extensions found.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

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

    by_ext_count: Dict[str, int] = defaultdict(int)
    by_ext_bytes: Dict[str, int] = defaultdict(int)
    total_bytes = 0
    total_files = 0

    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        ext = path.suffix.lower() or "(none)"
        by_ext_count[ext] += 1
        by_ext_bytes[ext] += size
        total_bytes += size
        total_files += 1

    if not by_ext_count:
        console.print(f"[yellow]No files found in[/yellow] {folder}")
        return 0

    table = Table(title=f"Extensions: {folder}", header_style="bold cyan")
    table.add_column("Extension", style="green")
    table.add_column("Count", justify="right", style="magenta")
    table.add_column("Size", justify="right", style="white")
    table.add_column("Bytes", justify="right", style="dim")

    for ext in sorted(by_ext_count.keys(), key=lambda e: (-by_ext_bytes[e], e)):
        table.add_row(
            ext,
            str(by_ext_count[ext]),
            format_size(by_ext_bytes[ext]),
            f"{by_ext_bytes[ext]:,}",
        )

    console.print(table)
    if not quiet:
        console.print(
            f"[bold]{total_files}[/bold] file(s), "
            f"[bold]{len(by_ext_count)}[/bold] extension(s), "
            f"[bold]{format_size(total_bytes)}[/bold]"
        )
    return len(by_ext_count)


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
