"""Command-line interface for :mod:`file_organizer`.

The CLI is deliberately preview-first.  Planning and duplicate discovery are
pure reads; moving files always requires either the ``apply`` command or the
explicit ``organize --apply`` compatibility flag.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from . import __version__
from .duplicates import find_duplicates
from .errors import OrganizerError
from .executor import apply_plan, apply_undo
from .history import create_undo_plan, list_history
from .models import SCHEMA_VERSION, DuplicateReport, OrganizationPlan, UndoPlan
from .planner import COLLISION_STRATEGIES, DUPLICATE_STRATEGIES, create_plan
from .rules import RuleSet, load_rules
from .utils import resolve_root

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class _UsageError(Exception):
    """A command-line usage error raised instead of exiting inside argparse."""

    def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
        super().__init__(message)
        self.parser = parser


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser with a concise, consistent usage error."""

    def error(self, message: str) -> None:
        raise _UsageError(self, message)


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit stable JSON instead of human-readable output.",
    )


def _add_scan_arguments(
    parser: argparse.ArgumentParser,
    *,
    configurable: bool = True,
) -> None:
    parser.add_argument("root", type=Path, help="Directory to inspect.")
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Include files in nested directories.",
    )
    parser.add_argument(
        "--ignore",
        "--ignore-pattern",
        metavar="GLOB",
        action="append",
        default=[],
        help="Ignore a path or filename glob; repeat for multiple patterns.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        default=None if configurable else False,
        help="Include hidden files and directories.",
    )
    if configurable:
        parser.add_argument(
            "-c",
            "--config",
            type=Path,
            help="Load categories and scan defaults from a JSON rules file.",
        )
    _add_json_argument(parser)


def _add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    _add_scan_arguments(parser)
    parser.add_argument(
        "--collision",
        "--collision-strategy",
        choices=COLLISION_STRATEGIES,
        default="rename",
        help="How to handle an occupied destination (default: rename).",
    )
    parser.add_argument(
        "--duplicates",
        "--duplicate-strategy",
        dest="duplicate_strategy",
        choices=DUPLICATE_STRATEGIES,
        default="keep",
        help="How to handle duplicate content (default: keep).",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build and return the public argument parser."""

    parser = _ArgumentParser(
        prog="file-organizer",
        description=(
            "Plan, apply, audit, and safely undo deterministic file organization."
        ),
        epilog=(
            "Start with 'file-organizer plan ~/Downloads', then use "
            "'file-organizer apply ~/Downloads' after reviewing the plan."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.set_defaults(json=False)
    _add_json_argument(parser)
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
    )

    for name, help_text in (
        ("plan", "Create a read-only organization plan."),
        ("preview", "Alias for plan; never moves files."),
    ):
        command = commands.add_parser(name, help=help_text, description=help_text)
        _add_plan_arguments(command)

    organize = commands.add_parser(
        "organize",
        help="Preview by default; move files only with --apply.",
        description=(
            "Compatibility workflow that previews safely unless --apply is present."
        ),
    )
    _add_plan_arguments(organize)
    mode = organize.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply the generated plan transactionally.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly request the default preview-only behavior.",
    )

    apply_command = commands.add_parser(
        "apply",
        help="Create and transactionally apply an organization plan.",
        description=(
            "Generate a fresh plan, verify every source, and apply it as one "
            "recorded transaction."
        ),
    )
    _add_plan_arguments(apply_command)

    undo = commands.add_parser(
        "undo",
        help="Preview or apply the undo of a recorded operation.",
        description="Undo the newest operation, or one selected by operation ID.",
    )
    undo.add_argument("root", type=Path, help="Directory whose history to use.")
    undo.add_argument(
        "--operation",
        metavar="ID",
        help="Undo this operation ID instead of the newest eligible operation.",
    )
    undo.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the undo plan without restoring files.",
    )
    _add_json_argument(undo)

    duplicates = commands.add_parser(
        "duplicates",
        help="Report byte-identical files without modifying them.",
        description=(
            "Find duplicate content using file size and SHA-256; no files are removed."
        ),
    )
    _add_scan_arguments(duplicates)

    history = commands.add_parser(
        "history",
        help="List recorded organization operations.",
        description="List transaction manifests for a directory, newest first.",
    )
    history.add_argument("root", type=Path, help="Directory whose history to list.")
    _add_json_argument(history)
    return parser


def _dump_json(payload: object, stream: TextIO | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")


def _human_size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _print_plan(plan: OrganizationPlan, *, heading: str = "Plan") -> None:
    payload = plan.to_dict()
    summary = payload["summary"]
    print(f"{heading}: {plan.root}")
    print(
        f"  {summary['planned_moves']} move(s) from {summary['scanned_files']} "
        f"scanned file(s), {_human_size(summary['bytes_to_move'])} total"
    )
    for move in payload["operations"]:
        print(f"  {move['source']} -> {move['destination']}")
    if payload["skipped"]:
        print(f"  Skipped: {summary['skipped']}")
        for item in payload["skipped"]:
            detail = f" ({item['detail']})" if item.get("detail") else ""
            print(f"    {item['path']}: {item['reason']}{detail}")
    if summary["duplicate_groups"]:
        print(f"  Duplicate groups detected: {summary['duplicate_groups']}")


def _print_duplicate_report(report: DuplicateReport) -> None:
    payload = report.to_dict()
    summary = payload["summary"]
    print(f"Duplicates: {report.root}")
    print(
        f"  {summary['duplicate_groups']} group(s), "
        f"{summary['duplicate_files']} file(s), "
        f"{_human_size(summary['reclaimable_bytes'])} potentially reclaimable"
    )
    for number, group in enumerate(payload["duplicates"], 1):
        print(f"  Group {number}: {_human_size(group['size'])} · {group['sha256']}")
        for path in group["files"]:
            print(f"    {path}")
    if summary["skipped"]:
        print(f"  Skipped entries: {summary['skipped']}")


def _print_undo_plan(plan: UndoPlan) -> None:
    payload = plan.to_dict()
    summary = payload["summary"]
    print(f"Undo plan: {plan.root}")
    print(f"  Operation: {payload['reverts_operation_id']}")
    print(
        f"  {summary['planned_moves']} restore(s), {summary['conflicts']} conflict(s)"
    )
    for move in payload["operations"]:
        print(f"  {move['current']} -> {move['original']}")
    for conflict in payload["conflicts"]:
        print(f"  Conflict: {conflict}", file=sys.stderr)


def _result_payload(result: object) -> Mapping[str, Any]:
    to_dict = getattr(result, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("transaction result does not support to_dict()")
    payload = to_dict()
    if not isinstance(payload, Mapping):
        raise TypeError("transaction result to_dict() must return a mapping")
    return payload


def _print_result(result: object, *, verb: str) -> None:
    payload = _result_payload(result)
    operation_id = payload.get(
        "operation_id", getattr(result, "operation_id", "unknown")
    )
    status = payload.get("status", getattr(result, "status", "complete"))
    summary = payload.get("summary", {})
    if not isinstance(summary, Mapping):
        summary = {}
    count = summary.get("moved")
    noun = "moved"
    if count is None:
        count = summary.get("restored", 0)
        noun = "restored"
    print(f"{verb}: {status}")
    print(f"  Operation: {operation_id}")
    print(f"  Files {noun}: {count}")
    manifest = payload.get("manifest")
    if manifest:
        print(f"  Manifest: {manifest}")


def _print_history(root: Path, entries: Sequence[Mapping[str, Any]]) -> None:
    print(f"History: {root}")
    if not entries:
        print("  No recorded operations.")
        return
    for entry in entries:
        operation_id = entry.get("operation_id", "unknown")
        status = entry.get("status", "unknown")
        timestamp = entry.get("created_at", entry.get("timestamp", "unknown time"))
        summary = entry.get("summary", {})
        count = summary.get("operations") if isinstance(summary, Mapping) else None
        suffix = f" · {count} file(s)" if count is not None else ""
        print(f"  {operation_id} · {status} · {timestamp}{suffix}")


def _load_plan(args: argparse.Namespace) -> OrganizationPlan:
    config = args.config.expanduser() if args.config else None
    rules: RuleSet = load_rules(config)
    protected = (config,) if config is not None else ()
    return create_plan(
        args.root,
        rules,
        recursive=args.recursive,
        collision_strategy=args.collision,
        duplicate_strategy=args.duplicate_strategy,
        ignore_patterns=args.ignore,
        include_hidden=args.include_hidden,
        protected_paths=protected,
    )


def _run(args: argparse.Namespace) -> int:
    command = args.command
    if command in {"plan", "preview", "organize", "apply"}:
        plan = _load_plan(args)
        should_apply = command == "apply" or (
            command == "organize" and bool(args.apply)
        )
        if not should_apply:
            if args.json:
                _dump_json(plan.to_dict())
            else:
                _print_plan(plan, heading="Preview")
                if command == "organize":
                    print("  Preview only; pass --apply to move these files.")
            return 0
        result = apply_plan(plan)
        if args.json:
            _dump_json(_result_payload(result))
        else:
            _print_result(result, verb="Apply")
        return 0

    if command == "duplicates":
        config = args.config.expanduser() if args.config else None
        rules = load_rules(config)
        include_hidden = (
            rules.include_hidden if args.include_hidden is None else args.include_hidden
        )
        report = find_duplicates(
            args.root,
            recursive=args.recursive,
            ignore_patterns=(*rules.ignore_patterns, *args.ignore),
            include_hidden=include_hidden,
        )
        if args.json:
            _dump_json(report.to_dict())
        else:
            _print_duplicate_report(report)
        return 0

    if command == "undo":
        plan = create_undo_plan(args.root, operation_id=args.operation)
        if args.dry_run:
            if args.json:
                _dump_json(plan.to_dict())
            else:
                _print_undo_plan(plan)
            return 0 if plan.safe_to_apply else EXIT_ERROR
        result = apply_undo(plan)
        if args.json:
            _dump_json(_result_payload(result))
        else:
            _print_result(result, verb="Undo")
        return 0

    if command == "history":
        root = resolve_root(args.root)
        entries = list_history(root)
        if args.json:
            _dump_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "history-report",
                    "root": str(root),
                    "summary": {"operations": len(entries)},
                    "operations": list(entries),
                }
            )
        else:
            _print_history(root, entries)
        return 0

    raise RuntimeError(f"unsupported command: {command}")


def _json_requested(argv: Sequence[str] | None) -> bool:
    values = sys.argv[1:] if argv is None else argv
    return "--json" in values


def _error_document(exc: BaseException) -> dict[str, Any]:
    error: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
    }
    conflicts = getattr(exc, "conflicts", None)
    if isinstance(conflicts, (list, tuple)) and conflicts:
        error["conflicts"] = [str(item) for item in conflicts]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "error",
        "error": error,
    }


def _usage_error_document(exc: _UsageError) -> dict[str, Any]:
    payload = _error_document(exc)
    payload["error"]["type"] = "UsageError"
    payload["error"]["usage"] = exc.parser.format_usage().strip()
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit status."""

    parser = build_parser()
    wants_json = _json_requested(argv)
    try:
        args = parser.parse_args(argv)
        return _run(args)
    except _UsageError as exc:
        if wants_json:
            _dump_json(_usage_error_document(exc), sys.stderr)
        else:
            exc.parser.print_usage(sys.stderr)
            print(f"{exc.parser.prog}: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        if wants_json:
            _dump_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "error",
                    "error": {
                        "type": "KeyboardInterrupt",
                        "message": "interrupted by user",
                    },
                },
                sys.stderr,
            )
        else:
            print("Interrupted by user.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except OrganizerError as exc:
        if wants_json:
            _dump_json(_error_document(exc), sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
            conflicts = getattr(exc, "conflicts", None)
            if isinstance(conflicts, (list, tuple)):
                for conflict in conflicts:
                    print(f"  - {conflict}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
