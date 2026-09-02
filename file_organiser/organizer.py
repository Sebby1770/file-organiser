"""Core logic for scanning, organizing, previewing, undoing, stats, and prune."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from rich.console import Console
from rich.table import Table
from rich.tree import Tree as RichTree

from .history import HISTORY_FILENAME, HistoryManager
from .report import write_report
from .rules import category_for_path
from .safety import (
    SafetyError,
    resolved_root,
    validate_category_name,
    validate_destination_path,
)
from .scanner import format_size, iter_files, matches_include, scan_folder
from .transaction import (
    PlanError,
    build_plan,
    execute_plan,
    read_plan,
    undo_latest,
)

ConflictStrategy = Literal["rename", "skip", "overwrite"]
DateSource = Literal["mtime", "ctime"]

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
    base = folder / validate_category_name(category)
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
    older_than: float | None = None,
    newer_than: float | None = None,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    on_conflict: ConflictStrategy = "rename",
    use_mime: bool = False,
    use_magic: bool = False,
    use_smart: bool = False,
    max_depth: int | None = None,
) -> Tuple[List[Tuple[Path, Path]], List[str]]:
    """Plan (src, dest) pairs without performing I/O beyond scanning/stat.

    Returns (pairs, skip_messages).
    """
    try:
        root = resolved_root(folder)
    except SafetyError as exc:
        return [], [str(exc)]
    grouped = scan_folder(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        older_than=older_than,
        newer_than=newer_than,
        use_mime=use_mime,
        use_magic=use_magic,
        use_smart=use_smart,
        max_depth=max_depth,
    )
    pairs: List[Tuple[Path, Path]] = []
    skips: List[str] = []
    # Track planned dests within this run to avoid collisions
    planned: set[Path] = set()

    for category, files in grouped.items():
        for src in files:
            try:
                dest_dir = target_directory(
                    root, category, src, by_date=by_date, date_source=date_source
                )
                dest = validate_destination_path(
                    root, dest_dir / src.name, allow_existing=True
                )
            except SafetyError as exc:
                skips.append(f"unsafe destination for {src.name}: {exc}")
                continue
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
    older_than: float | None = None,
    newer_than: float | None = None,
    by_date: bool = False,
    date_source: DateSource = "mtime",
    use_mime: bool = False,
    use_magic: bool = False,
    use_smart: bool = False,
    max_depth: int | None = None,
    on_conflict: ConflictStrategy = "rename",
    mode: str = "move",
    profile: str | None = None,
    content_hash: bool = False,
) -> Dict[str, Any]:
    """Build a versioned, fingerprinted, machine-readable organize plan."""
    pairs, skips = plan_moves(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        older_than=older_than,
        newer_than=newer_than,
        by_date=by_date,
        date_source=date_source,
        on_conflict=on_conflict,
        use_mime=use_mime,
        use_magic=use_magic,
        use_smart=use_smart,
        max_depth=max_depth,
    )
    rows: List[Tuple[Path, Path, str]] = []
    for src, dest in pairs:
        cat = category_for_path(src, rules, use_mime=use_mime, use_magic=use_magic, use_smart=use_smart)
        rows.append((src, dest, cat))
    return build_plan(
        folder,
        rows,
        mode=mode,
        on_conflict=on_conflict,
        skipped=skips,
        profile=profile,
        content_hash=content_hash,
    )


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
    use_magic: bool = False,
    use_smart: bool = False,
    max_depth: int | None = None,
    older_than: float | None = None,
    newer_than: float | None = None,
    quiet: bool = False,
    as_json: bool = False,
    on_conflict: ConflictStrategy = "rename",
    profile: str | None = None,
    content_hash: bool | None = None,
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
            use_magic=use_magic,
            use_smart=use_smart,
            max_depth=max_depth,
            older_than=older_than,
            newer_than=newer_than,
            on_conflict=on_conflict,
            profile=profile,
            # A JSON plan is potentially long-lived, so strong verification is
            # the safe default. In-memory/table previews stay metadata-only.
            content_hash=as_json if content_hash is None else content_hash,
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
        use_magic=use_magic,
        use_smart=use_smart,
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


def _prompt_category(
    path: Path,
    categories: Sequence[str],
    default: str = "Other",
) -> Optional[str]:
    """Prompt the user for a category. Returns None to skip the file."""
    cats = list(categories)
    if default not in cats:
        cats = list(cats) + [default]
    listing = ", ".join(cats)
    while True:
        try:
            raw = input(
                f"  {path.name} → category [{default}] "
                f"(or 'skip' / one of: {listing}): "
            ).strip()
        except EOFError:
            return default
        if not raw:
            return default
        if raw.lower() in ("skip", "s", "-"):
            return None
        # Case-insensitive match against known categories
        for c in cats:
            if c.lower() == raw.lower():
                return c
        # Allow free-form category names
        return raw


def plan_moves_interactive(
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
    on_conflict: ConflictStrategy = "rename",
    use_mime: bool = False,
    use_magic: bool = False,
    use_smart: bool = False,
    max_depth: int | None = None,
) -> Tuple[List[Tuple[Path, Path]], List[str]]:
    """Like plan_moves, but prompt for category on Other/unknown files."""
    from .rules import OTHER_CATEGORY

    grouped = scan_folder(
        folder,
        rules,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        use_mime=use_mime,
        use_magic=use_magic,
        use_smart=use_smart,
        max_depth=max_depth,
    )
    category_choices = sorted(rules.keys())
    if OTHER_CATEGORY not in category_choices:
        category_choices.append(OTHER_CATEGORY)

    # Flatten to (src, category) with interactive override for Other
    assignments: List[Tuple[Path, str]] = []
    other_files = list(grouped.get(OTHER_CATEGORY, []))
    if other_files:
        console.print(
            f"[cyan]Interactive:[/cyan] {len(other_files)} file(s) in "
            f"{OTHER_CATEGORY} — pick a category or skip "
            f"(default: {OTHER_CATEGORY})"
        )
    for category, files in grouped.items():
        if category != OTHER_CATEGORY:
            for src in files:
                assignments.append((src, category))
            continue
        for src in files:
            chosen = _prompt_category(src, category_choices, default=OTHER_CATEGORY)
            if chosen is None:
                console.print(f"  [dim]skipped[/dim] {src.name}")
                continue
            assignments.append((src, chosen))

    pairs: List[Tuple[Path, Path]] = []
    skips: List[str] = []
    planned: set[Path] = set()

    for src, category in assignments:
        try:
            dest_dir = target_directory(
                folder, category, src, by_date=by_date, date_source=date_source
            )
            dest = validate_destination_path(
                folder, dest_dir / src.name, allow_existing=True
            )
        except SafetyError as exc:
            skips.append(f"unsafe destination for {src.name}: {exc}")
            continue
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
                pass
            else:
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
    use_magic: bool = False,
    use_smart: bool = False,
    max_depth: int | None = None,
    prune_empty: bool = False,
    interactive: bool = False,
    quiet: bool = False,
    verbose: bool = False,
    force: bool = False,
    older_than: float | None = None,
    newer_than: float | None = None,
) -> int:
    """Transactionally move, copy, or symlink files into category folders.

    *symlink* creates a symlink at the destination pointing at the source
    (sources stay in place). Mutually preferred over copy when both set.

    When *interactive* is True, prompt for a category for each Other file.

    Returns the number of files successfully processed.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    if symlink and copy:
        # Symlink takes precedence; warn via quiet path only if verbose
        if verbose:
            console.print("[yellow]Both --symlink and --copy set; using symlink.[/yellow]")

    if interactive:
        pairs, skips = plan_moves_interactive(
            folder,
            rules,
            console,
            recursive=recursive,
            exclude=exclude,
            include=include,
            min_size=min_size,
            by_date=by_date,
            date_source=date_source,
            on_conflict=on_conflict,
            use_mime=use_mime,
            use_magic=use_magic,
            use_smart=use_smart,
            max_depth=max_depth,
            older_than=older_than,
            newer_than=newer_than,
        )
    else:
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
            use_magic=use_magic,
            use_smart=use_smart,
            max_depth=max_depth,
            older_than=older_than,
            newer_than=newer_than,
        )

    if not pairs and not skips:
        console.print(f"[yellow]Nothing to organize in[/yellow] {folder}")
        return 0

    if symlink:
        mode = "symlink"
    elif copy:
        mode = "copy"
    else:
        mode = "move"

    rows: List[Tuple[Path, Path, str]] = []
    try:
        root = resolved_root(folder)
        for src, dest in pairs:
            try:
                relative = dest.relative_to(root)
                category = relative.parts[0]
            except (ValueError, IndexError):
                category = category_for_path(
                    src,
                    rules,
                    use_mime=use_mime,
                    use_magic=use_magic,
                    use_smart=use_smart,
                )
            rows.append((src, dest, category))
        plan = build_plan(
            root,
            rows,
            mode=mode,
            on_conflict=on_conflict,
            skipped=skips,
        )
    except (PlanError, SafetyError, OSError, ValueError) as exc:
        console.print(f"[red]Safety check failed:[/red] {exc}")
        return 0

    total = len(rows)
    mode_tag = "[yellow](dry-run)[/yellow] " if dry_run else ""
    if not quiet:
        console.print(
            f"{mode_tag}Organizing [bold]{total}[/bold] file(s) in [cyan]{folder}[/cyan] "
            f"([dim]{mode}[/dim])"
        )

    if verbose:
        action = "would " + mode if dry_run else mode
        for src, dest in pairs:
            try:
                shown = dest.relative_to(root)
            except ValueError:
                shown = dest
            console.print(f"  [dim]{action}[/dim] {src.name} → {shown}")

    result = execute_plan(plan, dry_run=dry_run, force=force)
    success = result.completed
    if result.ok and not dry_run and not quiet:
        console.print(
            f"[green]✓[/green] Organized {success} file(s) atomically "
            f"([dim]{result.transaction_id[:8]}[/dim]). "
            f"Run [bold]undo[/bold] to revert."
        )
    elif result.ok and dry_run and not quiet:
        console.print(
            "[yellow]Dry run complete.[/yellow] "
            "All paths and fingerprints validated; no files were modified."
        )

    # Prune empty dirs after move-mode organize (not copy/symlink, not dry-run)
    if prune_empty and result.ok and not dry_run and mode == "move" and success:
        n = prune_empty_dirs(folder, console if not quiet else None, quiet=quiet)
        if quiet and n and console:
            pass  # stay quiet

    if report_path is not None:
        report_moves = [(dest, src) for src, dest in pairs] if result.ok else []
        write_report(
            report_path,
            report_moves,
            mode=mode,
            dry_run=dry_run,
            transaction_id=result.transaction_id or None,
            errors=result.errors,
        )
        if not quiet:
            console.print(f"[blue]Report written to[/blue] {report_path}")

    issues = list(skips) if verbose else []
    issues.extend(result.errors)
    if issues and not quiet:
        label = "Transaction rolled back" if result.rolled_back else "Safety check failed"
        console.print(f"[red]{label} ({len(issues)} issue(s)):[/red]")
        for err in issues:
            console.print(f"  [red]•[/red] {err}")

    return success


def undo(
    folder: Path,
    console: Console,
    *,
    quiet: bool = False,
    list_only: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Transactionally revert the most recent organize operation.

    For copy/symlink mode, removes the copies/links (does not delete originals).
    For move/rename mode, moves files back to original locations.

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

    snapshot = history.peek()
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
    result = undo_latest(folder, dry_run=dry_run, force=force)
    restored = result.completed

    # Clean up empty directories under the folder (category / date nests)
    if result.ok and not dry_run:
        _cleanup_empty_dirs(folder)

    remaining = history.load_stack()
    if not quiet:
        action = "Would remove" if dry_run and mode in ("copy", "symlink") else (
            "Would restore" if dry_run else (
                "Removed" if mode in ("copy", "symlink") else "Restored"
            )
        )
        extra = (
            f" ({len(remaining)} snapshot(s) remaining)"
            if remaining
            else ""
        )
        console.print(f"[green]✓[/green] {action} {restored} file(s).{extra}")
    if result.errors and not quiet:
        label = "Undo rolled back" if result.rolled_back else "Undo refused"
        console.print(f"[red]{label} ({len(result.errors)} error(s)):[/red]")
        for err in result.errors:
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


def apply_saved_plan(
    plan_path: Path,
    console: Console,
    *,
    dry_run: bool = True,
    force: bool = False,
) -> int:
    """Validate and atomically execute a saved organize plan."""
    try:
        data = read_plan(plan_path)
    except PlanError as exc:
        console.print(f"[red]Cannot read plan:[/red] {exc}")
        return -1
    result = execute_plan(data, dry_run=dry_run, force=force)
    if result.ok:
        verb = "validated" if dry_run else "applied"
        suffix = " — no files changed" if dry_run else f" — transaction {result.transaction_id[:8]}"
        console.print(f"[green]✓[/green] [bold]{verb} {result.completed}[/bold] file(s){suffix}")
    else:
        label = "Plan rolled back" if result.rolled_back else "Plan refused"
        console.print(f"[red]{label}:[/red]")
        for error in result.errors:
            console.print(f"  [red]•[/red] {error}")
    return result.completed if result.ok else -1
