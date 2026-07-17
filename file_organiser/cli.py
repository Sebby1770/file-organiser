"""Command-line interface for the file organiser."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from rich.console import Console
from rich.table import Table

from . import __version__
from .duplicates import default_workers, find_and_report_duplicates
from .organizer import (
    find_files,
    organize,
    preview,
    prune_empty_dirs,
    show_extensions,
    show_stats,
    show_tree,
    undo,
)
from .rules import OTHER_CATEGORY, discover_config, load_rules
from .scanner import parse_size


def _add_folder_arg(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "folder",
        type=Path,
        help="Path to the folder to operate on.",
    )


def _add_config_arg(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to a custom JSON rules config file. "
            "If omitted, looks for ./.file-organiser.json then "
            "~/.config/file-organiser/rules.json."
        ),
    )


def _add_verbosity(sp: argparse.ArgumentParser) -> None:
    g = sp.add_mutually_exclusive_group()
    g.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Minimal output.",
    )
    g.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output (log each file).",
    )


def _add_scan_opts(sp: argparse.ArgumentParser) -> None:
    """Common scan/filter options for organize/preview/duplicates/watch/stats."""
    sp.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Process nested folders (skip existing category folders).",
    )
    sp.add_argument(
        "--max-depth",
        type=int,
        default=None,
        metavar="N",
        help="Limit recursive scan depth (0 = top level only).",
    )
    sp.add_argument(
        "--min-size",
        type=str,
        default=None,
        metavar="SIZE",
        help="Skip files smaller than SIZE (e.g. 1K, 10M, 1G).",
    )
    sp.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Exclude files/dirs matching GLOB (repeatable).",
    )
    sp.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="Only process files matching GLOB (repeatable; inverse of --exclude).",
    )
    sp.add_argument(
        "--mime",
        action="store_true",
        help=(
            "When extension is unknown/missing, fall back to MIME type "
            "(mimetypes.guess_type) for categorization."
        ),
    )


def _add_organize_opts(sp: argparse.ArgumentParser) -> None:
    _add_scan_opts(sp)
    sp.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of moving them.",
    )
    sp.add_argument(
        "--symlink",
        action="store_true",
        help=(
            "Create symlinks in category folders instead of moving/copying "
            "(sources stay in place; symlinks are not followed when scanning)."
        ),
    )
    sp.add_argument(
        "--by-date",
        action="store_true",
        help="Nest under category/YYYY/MM using file modification time.",
    )
    sp.add_argument(
        "--date-source",
        choices=("mtime", "ctime"),
        default="mtime",
        help="Timestamp to use with --by-date (default: mtime).",
    )
    sp.add_argument(
        "--on-conflict",
        choices=("rename", "skip", "overwrite"),
        default="rename",
        help="When destination exists: rename (default), skip, or overwrite.",
    )
    sp.add_argument(
        "--report",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write a JSON, CSV, or Markdown report of all moves to PATH.",
    )
    sp.add_argument(
        "--prune-empty",
        action="store_true",
        help="After move-mode organize, remove empty directories left behind.",
    )
    _add_verbosity(sp)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="file-organiser",
        description=(
            "Smart CLI to sort, dedupe, and watch folders by type and date. "
            "Automatically sorts files into category folders (Images, Documents, …)."
        ),
        epilog="Example: file-organiser organize ~/Downloads --dry-run",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # --- organize ---
    sp_organize = subparsers.add_parser(
        "organize",
        help="Sort files into category subfolders.",
        description=(
            "Move (or copy/symlink) files in the target folder into category "
            "subfolders (Images, Documents, …)."
        ),
    )
    _add_folder_arg(sp_organize)
    _add_config_arg(sp_organize)
    _add_organize_opts(sp_organize)
    sp_organize.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without moving any files.",
    )

    # --- preview ---
    sp_preview = subparsers.add_parser(
        "preview",
        help="Show how files would be categorized, without moving them.",
        description="Print a table of categories and the files that would go into each.",
    )
    _add_folder_arg(sp_preview)
    _add_config_arg(sp_preview)
    _add_scan_opts(sp_preview)
    sp_preview.add_argument(
        "--by-date",
        action="store_true",
        help="Show date-nested destinations (category/YYYY/MM).",
    )
    sp_preview.add_argument(
        "--date-source",
        choices=("mtime", "ctime"),
        default="mtime",
        help="Timestamp to use with --by-date (default: mtime).",
    )
    sp_preview.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print a machine-readable organize plan as JSON to stdout.",
    )
    _add_verbosity(sp_preview)

    # --- find ---
    sp_find = subparsers.add_parser(
        "find",
        help="Find files by category, extension, or name pattern.",
        description=(
            "Search a folder and print matching paths with sizes. "
            "Filter with --category, --ext, and/or --name."
        ),
    )
    _add_folder_arg(sp_find)
    _add_config_arg(sp_find)
    _add_scan_opts(sp_find)
    sp_find.add_argument(
        "--category",
        type=str,
        default=None,
        metavar="NAME",
        help="Only files that categorize as NAME (e.g. Images).",
    )
    sp_find.add_argument(
        "--ext",
        type=str,
        default=None,
        metavar="EXT",
        help="Only files with this extension (e.g. .pdf or pdf).",
    )
    sp_find.add_argument(
        "--name",
        type=str,
        default=None,
        metavar="GLOB",
        help='Only files matching name glob (e.g. "*.invoice*").',
    )
    _add_verbosity(sp_find)

    # --- tree ---
    sp_tree = subparsers.add_parser(
        "tree",
        help="Show category folder tree with counts and sizes.",
        description=(
            "Display the current layout as a category tree "
            "(useful after organize, or to inspect an existing folder)."
        ),
    )
    _add_folder_arg(sp_tree)
    _add_config_arg(sp_tree)
    _add_scan_opts(sp_tree)
    sp_tree.set_defaults(recursive=True)
    sp_tree.add_argument(
        "--no-recursive",
        action="store_false",
        dest="recursive",
        help="Only scan the top level of the folder.",
    )
    _add_verbosity(sp_tree)

    # --- extensions ---
    sp_ext = subparsers.add_parser(
        "extensions",
        help="Inventory every extension with count and total size.",
        description=(
            "Scan a folder and print a table of each file extension with "
            "count and total bytes, sorted by size descending."
        ),
    )
    _add_folder_arg(sp_ext)
    # No config needed for pure extension inventory, but allow scan filters
    sp_ext.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        default=True,
        help="Scan nested folders (default: on).",
    )
    sp_ext.add_argument(
        "--no-recursive",
        action="store_false",
        dest="recursive",
        help="Only scan the top level of the folder.",
    )
    sp_ext.add_argument(
        "--max-depth",
        type=int,
        default=None,
        metavar="N",
        help="Limit recursive scan depth (0 = top level only).",
    )
    sp_ext.add_argument(
        "--min-size",
        type=str,
        default=None,
        metavar="SIZE",
        help="Skip files smaller than SIZE (e.g. 1K, 10M, 1G).",
    )
    sp_ext.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Exclude files/dirs matching GLOB (repeatable).",
    )
    sp_ext.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="Only process files matching GLOB (repeatable).",
    )
    _add_verbosity(sp_ext)

    # --- stats ---
    sp_stats = subparsers.add_parser(
        "stats",
        help="Show folder statistics by category and size.",
        description=(
            "Scan a folder and report total files, total size, breakdown by "
            "category, and the largest files."
        ),
    )
    _add_folder_arg(sp_stats)
    _add_config_arg(sp_stats)
    _add_scan_opts(sp_stats)
    sp_stats.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="Show top N largest files (default: 10).",
    )
    # stats defaults to recursive
    sp_stats.set_defaults(recursive=True)
    sp_stats.add_argument(
        "--no-recursive",
        action="store_false",
        dest="recursive",
        help="Only scan the top level of the folder.",
    )
    _add_verbosity(sp_stats)

    # --- undo ---
    sp_undo = subparsers.add_parser(
        "undo",
        help="Revert the last organize operation in a folder.",
        description=(
            "Use the history stack to move files back (or remove copies/symlinks). "
            "Supports multiple levels; use --list to inspect the stack."
        ),
    )
    _add_folder_arg(sp_undo)
    sp_undo.add_argument(
        "--list",
        action="store_true",
        dest="list_history",
        help="List undo history snapshots without reverting.",
    )
    _add_verbosity(sp_undo)

    # --- prune ---
    sp_prune = subparsers.add_parser(
        "prune",
        help="Remove empty directories under a folder.",
        description=(
            "Delete empty subdirectories only — never removes non-empty dirs "
            "or the root folder itself."
        ),
    )
    _add_folder_arg(sp_prune)
    sp_prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Show empty dirs that would be removed without deleting them.",
    )
    _add_verbosity(sp_prune)

    # --- duplicates ---
    sp_dup = subparsers.add_parser(
        "duplicates",
        help="Find duplicate files by content (SHA-256).",
        description=(
            "Scan for files with identical content and optionally delete extras. "
            "Uses an on-disk hash cache for faster re-runs."
        ),
    )
    _add_folder_arg(sp_dup)
    # Recursive by default for duplicates; --no-recursive to limit to top level
    sp_dup.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        default=True,
        help="Scan nested folders (default: on).",
    )
    sp_dup.add_argument(
        "--no-recursive",
        action="store_false",
        dest="recursive",
        help="Only scan the top level of the folder.",
    )
    sp_dup.add_argument(
        "--max-depth",
        type=int,
        default=None,
        metavar="N",
        help="Limit recursive scan depth (0 = top level only).",
    )
    sp_dup.add_argument(
        "--min-size",
        type=str,
        default=None,
        metavar="SIZE",
        help="Skip files smaller than SIZE (e.g. 1K, 10M, 1G).",
    )
    sp_dup.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Exclude files/dirs matching GLOB (repeatable).",
    )
    sp_dup.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="Only process files matching GLOB (repeatable).",
    )
    sp_dup.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=f"Parallel hash workers (default: min(8, cpu)={default_workers()}).",
    )
    sp_dup.add_argument(
        "--delete-dupes",
        action="store_true",
        help="Delete duplicate files, keeping one per group.",
    )
    sp_dup.add_argument(
        "--trash",
        action="store_true",
        help=(
            "With --delete-dupes, move duplicates to the OS trash "
            "(requires: pip install file-organiser[trash]). "
            "Falls back to permanent delete with a warning if unavailable."
        ),
    )
    sp_dup.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read/write the .organizer_hash_cache.json hash cache.",
    )
    sp_dup.add_argument(
        "--keep",
        choices=("oldest", "newest"),
        default="oldest",
        help="Which file to keep when deleting dupes (default: oldest).",
    )
    sp_dup.add_argument(
        "--dry-run",
        action="store_true",
        help="With --delete-dupes, show what would be deleted without deleting.",
    )
    _add_verbosity(sp_dup)

    # --- watch ---
    sp_watch = subparsers.add_parser(
        "watch",
        help="Watch a folder and auto-organize new files (requires watchdog).",
        description=(
            "Monitor a folder for new files and organize them automatically. "
            "Requires: pip install file-organiser[watch]"
        ),
    )
    _add_folder_arg(sp_watch)
    _add_config_arg(sp_watch)
    _add_organize_opts(sp_watch)

    # --- categories ---
    sp_cat = subparsers.add_parser(
        "categories",
        help="List default (or custom) category rules.",
        description="Print the category → extension mapping that would be used.",
    )
    _add_config_arg(sp_cat)

    return parser


def _resolve_min_size(value: Optional[str], console: Console) -> int:
    if not value:
        return 0
    try:
        return parse_size(value)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(2) from e


def _resolve_rules(config_arg: Optional[Path]) -> dict:
    """Load rules using explicit config or discovery."""
    path = discover_config(config_arg)
    return load_rules(path)


def list_categories(console: Console, config: Optional[Path] = None) -> None:
    path = discover_config(config)
    rules = load_rules(path)
    table = Table(title="File categories", header_style="bold cyan")
    table.add_column("Category", style="green")
    table.add_column("Extensions", style="white")
    for name in sorted(rules.keys()):
        exts = ", ".join(rules[name])
        table.add_row(name, exts)
    table.add_row(OTHER_CATEGORY, "[dim](anything unmatched)[/dim]")
    console.print(table)
    src = str(path) if path else "built-in defaults"
    console.print(f"[dim]{len(rules)} categories (+ {OTHER_CATEGORY}) — config: {src}[/dim]")


def main(argv: Optional[Sequence[str]] = None) -> int:
    console = Console()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "categories":
            cfg = args.config.expanduser().resolve() if args.config else None
            list_categories(console, cfg)
            return 0

        folder: Path = args.folder.expanduser().resolve()
        quiet = getattr(args, "quiet", False)
        verbose = getattr(args, "verbose", False)

        if args.command == "undo":
            undo(
                folder,
                console,
                quiet=quiet,
                list_only=getattr(args, "list_history", False),
            )
            return 0

        if args.command == "prune":
            prune_empty_dirs(
                folder,
                console,
                dry_run=getattr(args, "dry_run", False),
                quiet=quiet,
            )
            return 0

        if args.command == "duplicates":
            min_size = _resolve_min_size(getattr(args, "min_size", None), console)
            find_and_report_duplicates(
                folder,
                console,
                recursive=args.recursive,
                exclude=getattr(args, "exclude", None) or None,
                include=getattr(args, "include", None) or None,
                min_size=min_size,
                max_depth=getattr(args, "max_depth", None),
                workers=getattr(args, "workers", None),
                delete_dupes=args.delete_dupes,
                keep=args.keep,
                dry_run=args.dry_run,
                use_trash=getattr(args, "trash", False),
                use_cache=not getattr(args, "no_cache", False),
            )
            return 0

        if args.command == "extensions":
            min_size = _resolve_min_size(getattr(args, "min_size", None), console)
            show_extensions(
                folder,
                console,
                recursive=args.recursive,
                exclude=getattr(args, "exclude", None) or None,
                include=getattr(args, "include", None) or None,
                min_size=min_size,
                max_depth=getattr(args, "max_depth", None),
                quiet=quiet,
            )
            return 0

        # Rules needed for organize / preview / watch / stats / find / tree
        config_arg = (
            args.config.expanduser().resolve() if getattr(args, "config", None) else None
        )
        rules = _resolve_rules(config_arg)
        min_size = _resolve_min_size(getattr(args, "min_size", None), console)
        exclude: List[str] = list(getattr(args, "exclude", None) or [])
        include: List[str] = list(getattr(args, "include", None) or [])
        use_mime = bool(getattr(args, "mime", False))
        max_depth = getattr(args, "max_depth", None)

        if args.command == "stats":
            show_stats(
                folder,
                rules,
                console,
                recursive=args.recursive,
                exclude=exclude or None,
                include=include or None,
                min_size=min_size,
                use_mime=use_mime,
                max_depth=max_depth,
                top_n=getattr(args, "top", 10),
                quiet=quiet,
            )
            return 0

        if args.command == "find":
            find_files(
                folder,
                rules,
                console,
                category=getattr(args, "category", None),
                ext=getattr(args, "ext", None),
                name=getattr(args, "name", None),
                recursive=args.recursive,
                exclude=exclude or None,
                include=include or None,
                min_size=min_size,
                max_depth=max_depth,
                use_mime=use_mime,
                quiet=quiet,
            )
            return 0

        if args.command == "tree":
            show_tree(
                folder,
                rules,
                console,
                recursive=args.recursive,
                exclude=exclude or None,
                include=include or None,
                min_size=min_size,
                max_depth=max_depth,
                use_mime=use_mime,
                quiet=quiet,
            )
            return 0

        if args.command == "preview":
            preview(
                folder,
                rules,
                console,
                recursive=args.recursive,
                exclude=exclude or None,
                include=include or None,
                min_size=min_size,
                by_date=getattr(args, "by_date", False),
                date_source=getattr(args, "date_source", "mtime"),
                use_mime=use_mime,
                max_depth=max_depth,
                quiet=quiet,
                as_json=getattr(args, "as_json", False),
            )
            return 0

        if args.command == "organize":
            organize(
                folder,
                rules,
                console,
                dry_run=args.dry_run,
                recursive=args.recursive,
                copy=args.copy,
                symlink=getattr(args, "symlink", False),
                by_date=args.by_date,
                date_source=args.date_source,
                min_size=min_size,
                exclude=exclude or None,
                include=include or None,
                on_conflict=args.on_conflict,
                report_path=args.report.expanduser().resolve() if args.report else None,
                use_mime=use_mime,
                max_depth=max_depth,
                prune_empty=getattr(args, "prune_empty", False),
                quiet=quiet,
                verbose=verbose,
            )
            return 0

        if args.command == "watch":
            from .watch import watch_folder

            return watch_folder(
                folder,
                console,
                rules=rules,
                recursive=args.recursive,
                copy=args.copy,
                by_date=args.by_date,
                min_size=min_size,
                exclude=exclude or None,
                on_conflict=args.on_conflict,
                quiet=quiet,
            )

        parser.print_help()
        return 1

    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1
    except ValueError as e:
        console.print(f"[red]Config error:[/red] {e}")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        return 130
    except Exception as e:  # last-resort safety net
        console.print(f"[red]Unexpected error:[/red] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
