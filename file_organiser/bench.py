"""Light benchmark: time scan + hash of first N files."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Sequence

from rich.console import Console
from rich.table import Table

from .duplicates import file_sha256
from .scanner import format_size, iter_files


def benchmark_folder(
    folder: Path,
    console: Console,
    *,
    limit: int = 100,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    max_depth: int | None = None,
    quiet: bool = False,
) -> dict:
    """Time file scan and hashing of the first *limit* files.

    Returns a stats dict with timings and throughput.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return {}

    t0 = time.perf_counter()
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        include=None,
        min_size=0,
        category_names=None,
        skip_category_folders=False,
        max_depth=max_depth,
    )
    t_scan = time.perf_counter() - t0

    sample = sorted(files, key=lambda p: str(p))[: max(1, limit)]
    total_bytes = 0
    hashed = 0
    errors = 0

    t1 = time.perf_counter()
    for path in sample:
        try:
            size = path.stat().st_size
            file_sha256(path)
            total_bytes += size
            hashed += 1
        except OSError:
            errors += 1
    t_hash = time.perf_counter() - t1
    t_total = time.perf_counter() - t0

    scan_rate = (len(files) / t_scan) if t_scan > 0 else 0.0
    hash_mibs = (total_bytes / (1024 * 1024) / t_hash) if t_hash > 0 else 0.0
    hash_fps = (hashed / t_hash) if t_hash > 0 else 0.0

    stats = {
        "files_found": len(files),
        "files_hashed": hashed,
        "bytes_hashed": total_bytes,
        "scan_seconds": t_scan,
        "hash_seconds": t_hash,
        "total_seconds": t_total,
        "scan_files_per_sec": scan_rate,
        "hash_mib_per_sec": hash_mibs,
        "hash_files_per_sec": hash_fps,
        "errors": errors,
        "limit": limit,
    }

    table = Table(title=f"Benchmark: {folder}", header_style="bold cyan")
    table.add_column("Metric", style="green")
    table.add_column("Value", justify="right", style="white")

    table.add_row("Files found (scan)", str(stats["files_found"]))
    table.add_row("Files hashed", f"{hashed} (limit={limit})")
    table.add_row("Bytes hashed", f"{format_size(total_bytes)} ({total_bytes:,})")
    table.add_row("Scan time", f"{t_scan * 1000:.1f} ms")
    table.add_row("Hash time", f"{t_hash * 1000:.1f} ms")
    table.add_row("Total time", f"{t_total * 1000:.1f} ms")
    table.add_row("Scan throughput", f"{scan_rate:.0f} files/s")
    table.add_row("Hash throughput", f"{hash_mibs:.2f} MiB/s ({hash_fps:.1f} files/s)")
    if errors:
        table.add_row("Errors", str(errors))

    console.print(table)
    if not quiet:
        console.print(
            f"[dim]Hashed first {hashed} of {len(files)} file(s). "
            f"Use --limit N to change sample size.[/dim]"
        )
    return stats
