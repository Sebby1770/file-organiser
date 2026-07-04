"""Core logic for scanning, organizing, previewing, and undoing."""
from __future__ import annotations

import os
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.request import Request, urlopen

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
from .runlog import RunLog
from .rules import OTHER_CATEGORY, category_for_extension

QUARANTINE_CATEGORY = "Quarantine"
DUPLICATES_CATEGORY = "Duplicates"


@dataclass(frozen=True)
class OrganizeOptions:
    recursive: bool = False
    partition_by_date: bool = False
    quarantine_unknown: bool = False
    dedupe: bool = False
    json_log: Path | None = None
    db_path: Path | None = None
    max_files: int | None = None
    min_age_seconds: int = 0
    redact_paths: bool = False


@dataclass(frozen=True)
class PlannedMove:
    source: Path
    category: str
    target_dir: Path
    checksum: str | None = None


def _iter_candidate_files(folder: Path, rules: Dict[str, List[str]], recursive: bool) -> Iterable[Path]:
    entries = folder.rglob("*") if recursive else folder.iterdir()
    generated_dirs = set(rules.keys()) | {OTHER_CATEGORY, QUARANTINE_CATEGORY, DUPLICATES_CATEGORY}

    for entry in entries:
        if not entry.is_file():
            continue
        relative = entry.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if entry.name == HISTORY_FILENAME:
            continue
        if recursive and relative.parts and relative.parts[0] in generated_dirs:
            continue
        yield entry


def _partition_for_file(src: Path) -> str:
    return datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m")


def _file_checksum(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(path: Path, folder: Path, redact: bool) -> str:
    if not redact:
        return str(path)
    try:
        return str(path.relative_to(folder))
    except ValueError:
        return path.name


def _scan_folder(folder: Path, rules: Dict[str, List[str]], options: OrganizeOptions) -> List[PlannedMove]:
    """Build a list of planned file moves."""
    planned: List[PlannedMove] = []
    seen_checksums: set[str] = set()

    for entry in _iter_candidate_files(folder, rules, options.recursive):
        if options.json_log and entry.resolve() == options.json_log.resolve():
            continue
        if options.db_path and entry.resolve() == options.db_path.resolve():
            continue
        if options.min_age_seconds > 0 and time.time() - entry.stat().st_mtime < options.min_age_seconds:
            continue
        checksum = _file_checksum(entry) if options.dedupe else None
        category = category_for_extension(entry.suffix, rules)
        if checksum and checksum in seen_checksums:
            category = DUPLICATES_CATEGORY
        elif checksum:
            seen_checksums.add(checksum)
        if options.quarantine_unknown and category == OTHER_CATEGORY:
            category = QUARANTINE_CATEGORY
        target_dir = folder / category
        if options.partition_by_date:
            target_dir = target_dir / _partition_for_file(entry)
        planned.append(PlannedMove(entry, category, target_dir, checksum))

        if options.max_files is not None and len(planned) >= options.max_files:
            break

    return planned


def build_manifest(folder: Path, rules: Dict[str, List[str]], options: OrganizeOptions) -> dict:
    planned = _scan_folder(folder, rules, options)
    files = []
    total_bytes = 0
    category_bytes: dict[str, int] = defaultdict(int)
    for move in planned:
        stat = move.source.stat()
        target = move.target_dir / move.source.name
        total_bytes += stat.st_size
        category_bytes[move.category] += stat.st_size
        files.append(
            {
                "source": _manifest_path(move.source, folder, options.redact_paths),
                "target": _manifest_path(target, folder, options.redact_paths),
                "category": move.category,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "checksum": move.checksum,
            }
        )
    grouped = _group_planned(planned)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": "[redacted]" if options.redact_paths else str(folder),
        "root_hash": sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:16],
        "total_files": len(files),
        "total_bytes": total_bytes,
        "categories": {category: len(items) for category, items in sorted(grouped.items())},
        "category_bytes": {category: category_bytes[category] for category in sorted(category_bytes)},
        "options": {
            "recursive": options.recursive,
            "partition_by_date": options.partition_by_date,
            "quarantine_unknown": options.quarantine_unknown,
            "dedupe": options.dedupe,
            "min_age_seconds": options.min_age_seconds,
            "redact_paths": options.redact_paths,
        },
        "files": files,
    }


def _clean_supabase_table(value: str | None) -> str:
    cleaned = "".join(character for character in str(value or "") if character.isalnum() or character == "_")
    return cleaned or "organizer_manifests"


def sync_manifest_to_supabase(payload: dict, table: str | None = None) -> dict:
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret_key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not supabase_url or not secret_key:
        raise ValueError("Set SUPABASE_URL and SUPABASE_SECRET_KEY before using --supabase-sync.")

    table_name = _clean_supabase_table(table or os.getenv("SUPABASE_MANIFEST_TABLE", "organizer_manifests"))
    body = {
        "source": "file-organizer-cli",
        "root": payload["root"],
        "file_count": payload["total_files"],
        "total_bytes": payload["total_bytes"],
        "category_counts": payload["categories"],
        "manifest": payload,
    }
    request = Request(
        f"{supabase_url}/rest/v1/{table_name}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urlopen(request, timeout=float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "10"))) as response:
        status = int(getattr(response, "status", 201))
        if status >= 400:
            raise RuntimeError(f"Supabase REST API returned HTTP {status}")
    return {"ok": True, "table": table_name, "file_count": payload["total_files"]}


def manifest(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    options: OrganizeOptions | None = None,
    output: Path | None = None,
    sync_supabase: bool = False,
    supabase_table: str | None = None,
) -> None:
    options = options or OrganizeOptions()
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    payload = build_manifest(folder, rules, options)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[green]✓[/green] Wrote manifest for {payload['total_files']} file(s) to [cyan]{output}[/cyan]")
    else:
        console.print(json.dumps(payload, indent=2))

    if sync_supabase:
        result = sync_manifest_to_supabase(payload, supabase_table)
        console.print(
            f"[green]✓[/green] Synced manifest for {result['file_count']} file(s) "
            f"to Supabase table [cyan]{result['table']}[/cyan]"
        )


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


def _group_planned(planned: List[PlannedMove]) -> Dict[str, List[PlannedMove]]:
    grouped: Dict[str, List[PlannedMove]] = defaultdict(list)
    for move in planned:
        grouped[move.category].append(move)
    return grouped


def _write_json_event(path: Path | None, event: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": datetime.now().isoformat(timespec="seconds"), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _cleanup_empty_dirs(folder: Path) -> None:
    directories = sorted(
        (entry for entry in folder.rglob("*") if entry.is_dir()),
        key=lambda path: len(path.relative_to(folder).parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def preview(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    options: OrganizeOptions | None = None,
) -> None:
    """Show what would be organized, without moving anything."""
    options = options or OrganizeOptions()
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    planned = _scan_folder(folder, rules, options)
    grouped = _group_planned(planned)
    if not grouped:
        console.print(f"[yellow]No files to organize in[/yellow] {folder}")
        return

    table = Table(title=f"Preview: {folder}", header_style="bold cyan")
    table.add_column("Category", style="green")
    table.add_column("Count", justify="right", style="magenta")
    table.add_column("Target", style="cyan")
    table.add_column("Example files", style="white")

    total = 0
    for category in sorted(grouped.keys()):
        moves = grouped[category]
        files = [move.source for move in moves]
        total += len(files)
        examples = ", ".join(f.name for f in files[:3])
        if len(files) > 3:
            examples += f", ... (+{len(files) - 3} more)"
        targets = sorted({str(move.target_dir.relative_to(folder)) for move in moves})
        target = ", ".join(targets[:2])
        if len(targets) > 2:
            target += f", ... (+{len(targets) - 2} more)"
        table.add_row(category, str(len(files)), target, examples)

    console.print(table)
    console.print(f"[bold]Total:[/bold] {total} file(s) across {len(grouped)} categor(ies)")


def organize(
    folder: Path,
    rules: Dict[str, List[str]],
    console: Console,
    dry_run: bool = False,
    options: OrganizeOptions | None = None,
) -> None:
    """Move files into category subfolders. Use dry_run to simulate."""
    options = options or OrganizeOptions()
    started_at = time.perf_counter()
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return

    planned = _scan_folder(folder, rules, options)
    if not planned:
        console.print(f"[yellow]Nothing to organize in[/yellow] {folder}")
        return

    total = len(planned)
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

        for planned_move in planned:
            target_dir = planned_move.target_dir
            if not dry_run:
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    errors.append(f"Could not create {target_dir}: {e}")
                    progress.advance(task, 1)
                    continue

            src = planned_move.source
            dest = _unique_destination(target_dir / src.name)
            try:
                if dry_run:
                    progress.console.log(f"[dim]would move[/dim] {src} -> {target_dir.relative_to(folder)}/")
                    _write_json_event(
                        options.json_log,
                        {
                            "event": "dry_run_move",
                            "source": src,
                            "destination": dest,
                            "category": planned_move.category,
                            "checksum": planned_move.checksum,
                        },
                    )
                else:
                    shutil.move(str(src), str(dest))
                    moves.append((dest, src))  # (current_location, original_location)
                    _write_json_event(
                        options.json_log,
                        {
                            "event": "move",
                            "source": src,
                            "destination": dest,
                            "category": planned_move.category,
                            "checksum": planned_move.checksum,
                        },
                    )
            except (OSError, shutil.Error) as e:
                message = f"Failed to move {src.name}: {e}"
                errors.append(message)
                _write_json_event(options.json_log, {"event": "error", "source": src, "error": message})
            finally:
                progress.advance(task, 1)

    if not dry_run and moves:
        HistoryManager(folder).save(
            moves,
            metadata={
                "recursive": options.recursive,
                "partition_by_date": options.partition_by_date,
                "quarantine_unknown": options.quarantine_unknown,
                "dedupe": options.dedupe,
            },
            errors=errors,
        )
        console.print(f"[green]✓[/green] Organized {len(moves)} file(s). Run [bold]undo[/bold] to revert.")
    elif dry_run:
        console.print("[yellow]Dry run complete.[/yellow] No files were moved.")

    duration = max(0.001, time.perf_counter() - started_at)
    moved_count = len(moves) if not dry_run else total
    throughput = moved_count / duration
    console.print(f"[bold]Throughput:[/bold] {throughput:.2f} file(s)/second over {duration:.2f}s")
    RunLog(folder, options.db_path).record(
        command="organize",
        dry_run=dry_run,
        planned=total,
        moved=moved_count,
        errors=len(errors),
        duration_seconds=duration,
        metadata={
            "recursive": options.recursive,
            "partition_by_date": options.partition_by_date,
            "quarantine_unknown": options.quarantine_unknown,
            "dedupe": options.dedupe,
        },
    )

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

    _cleanup_empty_dirs(folder)

    history.clear()
    console.print(f"[green]✓[/green] Restored {restored} file(s).")
    if errors:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
