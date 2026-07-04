"""Command-line interface for the file organizer."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .organizer import OrganizeOptions, manifest, organize, preview, undo
from .runlog import RunLog
from .rules import load_rules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="file-organizer",
        description=(
            "Smart file organizer CLI. Automatically sorts files in a folder "
            "by type (images, documents, videos, code, etc)."
        ),
        epilog="Example: python main.py organize ~/Downloads --dry-run",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # Shared arguments for all subcommands.
    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "folder",
            type=Path,
            help="Path to the folder to operate on.",
        )
        sp.add_argument(
            "-c", "--config",
            type=Path,
            default=None,
            help="Path to a custom JSON rules config file.",
        )

    def add_scan_options(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "-r",
            "--recursive",
            action="store_true",
            help="Scan nested folders while skipping generated category folders.",
        )
        sp.add_argument(
            "--partition-by-date",
            action="store_true",
            help="Partition destinations by file modified month, e.g. Images/2026-06/.",
        )
        sp.add_argument(
            "--quarantine-unknown",
            action="store_true",
            help="Send unknown extensions to Quarantine instead of Other.",
        )
        sp.add_argument(
            "--dedupe",
            action="store_true",
            help="Hash files and route duplicates to a Duplicates folder.",
        )
        sp.add_argument(
            "--max-files",
            type=int,
            default=None,
            help="Safety cap for the number of files processed in one run.",
        )
        sp.add_argument(
            "--min-age-seconds",
            type=int,
            default=0,
            help="Skip files modified more recently than this many seconds.",
        )

    # organize
    sp_organize = subparsers.add_parser(
        "organize",
        help="Sort files into category subfolders.",
        description="Move files in the target folder into category subfolders (Images, Documents, ...).",
    )
    add_common(sp_organize)
    add_scan_options(sp_organize)
    sp_organize.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without moving any files.",
    )
    sp_organize.add_argument(
        "--json-log",
        type=Path,
        default=None,
        help="Append JSONL audit events to this file.",
    )
    sp_organize.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite run-log database path. Defaults to .organizer_runs.sqlite3 in the target folder.",
    )

    # preview
    sp_preview = subparsers.add_parser(
        "preview",
        help="Show how files would be categorized, without moving them.",
        description="Print a table of categories and the files that would go into each.",
    )
    add_common(sp_preview)
    add_scan_options(sp_preview)

    sp_manifest = subparsers.add_parser(
        "manifest",
        help="Create a JSON inventory of planned file moves.",
        description="Scan files and output a JSON manifest with categories, targets, sizes, mtimes, and optional checksums.",
    )
    add_common(sp_manifest)
    add_scan_options(sp_manifest)
    sp_manifest.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write the manifest JSON to this path instead of stdout.",
    )

    # undo
    sp_undo = subparsers.add_parser(
        "undo",
        help="Revert the last organize operation in a folder.",
        description="Use the history file to move files back to their original locations.",
    )
    sp_undo.add_argument(
        "folder",
        type=Path,
        help="Path to the folder that was previously organized.",
    )

    sp_history = subparsers.add_parser(
        "history",
        help="Show recent organize runs from the embedded SQLite run log.",
        description="Read the SQLite run log and print recent run metadata.",
    )
    sp_history.add_argument("folder", type=Path, help="Path to the organized folder.")
    sp_history.add_argument("--limit", type=int, default=10, help="Number of runs to show.")
    sp_history.add_argument("--db", type=Path, default=None, help="SQLite run-log database path.")

    return parser


def main(argv: list[str] | None = None) -> int:
    console = Console()
    parser = build_parser()
    args = parser.parse_args(argv)

    folder: Path = args.folder.expanduser().resolve()

    try:
        if args.command == "undo":
            undo(folder, console)
            return 0

        if args.command == "history":
            if args.limit < 1:
                raise ValueError("--limit must be greater than zero.")
            db_path = args.db.expanduser().resolve() if args.db else None
            rows = RunLog(folder, db_path).recent(limit=args.limit)
            table = Table(title=f"Organizer history: {folder}", header_style="bold cyan")
            table.add_column("ID", justify="right")
            table.add_column("Created")
            table.add_column("Mode")
            table.add_column("Planned", justify="right")
            table.add_column("Moved", justify="right")
            table.add_column("Errors", justify="right")
            table.add_column("Files/s", justify="right")
            for row in rows:
                rate = row["moved"] / max(0.001, row["duration_seconds"])
                table.add_row(
                    str(row["id"]),
                    row["created_at"],
                    "dry-run" if row["dry_run"] else row["command"],
                    str(row["planned"]),
                    str(row["moved"]),
                    str(row["errors"]),
                    f"{rate:.2f}",
                )
            console.print(table)
            return 0

        rules = load_rules(args.config.expanduser().resolve() if args.config else None)

        options = OrganizeOptions(
            recursive=getattr(args, "recursive", False),
            partition_by_date=getattr(args, "partition_by_date", False),
            quarantine_unknown=getattr(args, "quarantine_unknown", False),
            dedupe=getattr(args, "dedupe", False),
            json_log=args.json_log.expanduser().resolve() if getattr(args, "json_log", None) else None,
            db_path=args.db.expanduser().resolve() if getattr(args, "db", None) else None,
            max_files=getattr(args, "max_files", None),
            min_age_seconds=getattr(args, "min_age_seconds", 0),
        )
        if options.max_files is not None and options.max_files < 1:
            raise ValueError("--max-files must be greater than zero.")
        if options.min_age_seconds < 0:
            raise ValueError("--min-age-seconds cannot be negative.")

        if args.command == "preview":
            preview(folder, rules, console, options=options)
        elif args.command == "manifest":
            manifest(
                folder,
                rules,
                console,
                options=options,
                output=args.output.expanduser().resolve() if args.output else None,
            )
        elif args.command == "organize":
            organize(folder, rules, console, dry_run=args.dry_run, options=options)
        else:
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
