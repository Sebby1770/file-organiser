"""Command-line interface for the file organizer."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from . import __version__
from .organizer import organize, preview, undo
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

    # organize
    sp_organize = subparsers.add_parser(
        "organize",
        help="Sort files into category subfolders.",
        description="Move files in the target folder into category subfolders (Images, Documents, ...).",
    )
    add_common(sp_organize)
    sp_organize.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without moving any files.",
    )

    # preview
    sp_preview = subparsers.add_parser(
        "preview",
        help="Show how files would be categorized, without moving them.",
        description="Print a table of categories and the files that would go into each.",
    )
    add_common(sp_preview)

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

        rules = load_rules(args.config.expanduser().resolve() if args.config else None)

        if args.command == "preview":
            preview(folder, rules, console)
        elif args.command == "organize":
            organize(folder, rules, console, dry_run=args.dry_run)
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
