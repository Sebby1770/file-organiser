"""Compare two folders: only-in-A, only-in-B, same name different hash, identical."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from rich.console import Console
from rich.table import Table

from .duplicates import file_sha256
from .scanner import format_size, iter_files


def _index_files(
    folder: Path,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    max_depth: int | None = None,
) -> Dict[str, Path]:
    """Map relative POSIX path → absolute Path for files under *folder*."""
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
    index: Dict[str, Path] = {}
    for path in files:
        try:
            rel = path.relative_to(folder).as_posix()
        except ValueError:
            rel = path.name
        index[rel] = path
    return index


def _safe_hash(path: Path) -> Optional[str]:
    try:
        return file_sha256(path)
    except OSError:
        return None


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def compare_folders(
    folder_a: Path,
    folder_b: Path,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    max_depth: int | None = None,
) -> Dict[str, List]:
    """Compare two folders by relative path and content hash.

    Returns dict with keys:
      - only_a: list of relative paths
      - only_b: list of relative paths
      - different: list of (rel, hash_a, hash_b) where name matches but content differs
      - identical: list of relative paths with same content hash
    """
    idx_a = _index_files(
        folder_a, recursive=recursive, exclude=exclude, max_depth=max_depth
    )
    idx_b = _index_files(
        folder_b, recursive=recursive, exclude=exclude, max_depth=max_depth
    )

    keys_a = set(idx_a)
    keys_b = set(idx_b)

    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    common = sorted(keys_a & keys_b)

    different: List[Tuple[str, str, str]] = []
    identical: List[str] = []

    for rel in common:
        ha = _safe_hash(idx_a[rel])
        hb = _safe_hash(idx_b[rel])
        if ha is None or hb is None:
            # Treat unreadable as different
            different.append((rel, ha or "?", hb or "?"))
        elif ha == hb:
            identical.append(rel)
        else:
            different.append((rel, ha, hb))

    return {
        "only_a": only_a,
        "only_b": only_b,
        "different": different,
        "identical": identical,
        "paths_a": idx_a,
        "paths_b": idx_b,
    }


def diff_folders(
    folder_a: Path,
    folder_b: Path,
    console: Console,
    *,
    recursive: bool = True,
    exclude: Sequence[str] | None = None,
    max_depth: int | None = None,
    quiet: bool = False,
) -> Dict[str, int]:
    """Compare two folders and print a report. Returns counts per bucket."""
    if not folder_a.exists() or not folder_a.is_dir():
        console.print(f"[red]Error:[/red] '{folder_a}' is not a valid directory.")
        return {"only_a": 0, "only_b": 0, "different": 0, "identical": 0}
    if not folder_b.exists() or not folder_b.is_dir():
        console.print(f"[red]Error:[/red] '{folder_b}' is not a valid directory.")
        return {"only_a": 0, "only_b": 0, "different": 0, "identical": 0}

    console.print(
        f"Comparing [cyan]{folder_a}[/cyan] vs [cyan]{folder_b}[/cyan] ..."
    )
    result = compare_folders(
        folder_a,
        folder_b,
        recursive=recursive,
        exclude=exclude,
        max_depth=max_depth,
    )

    only_a: List[str] = result["only_a"]
    only_b: List[str] = result["only_b"]
    different: List[Tuple[str, str, str]] = result["different"]
    identical: List[str] = result["identical"]
    paths_a: Dict[str, Path] = result["paths_a"]
    paths_b: Dict[str, Path] = result["paths_b"]

    if only_a:
        table = Table(title=f"Only in A ({folder_a.name})", header_style="bold cyan")
        table.add_column("Path", style="white")
        table.add_column("Size", justify="right", style="magenta")
        for rel in only_a:
            table.add_row(rel, format_size(_safe_size(paths_a[rel])))
        console.print(table)

    if only_b:
        table = Table(title=f"Only in B ({folder_b.name})", header_style="bold cyan")
        table.add_column("Path", style="white")
        table.add_column("Size", justify="right", style="magenta")
        for rel in only_b:
            table.add_row(rel, format_size(_safe_size(paths_b[rel])))
        console.print(table)

    if different:
        table = Table(
            title="Same name, different content",
            header_style="bold yellow",
        )
        table.add_column("Path", style="white")
        table.add_column("Hash A", style="dim")
        table.add_column("Hash B", style="dim")
        for rel, ha, hb in different:
            table.add_row(rel, ha[:12] if ha != "?" else "?", hb[:12] if hb != "?" else "?")
        console.print(table)

    if identical and not quiet:
        table = Table(title="Identical (same content)", header_style="bold green")
        table.add_column("Path", style="white")
        for rel in identical[:50]:
            table.add_row(rel)
        if len(identical) > 50:
            table.add_row(f"… (+{len(identical) - 50} more)")
        console.print(table)

    counts = {
        "only_a": len(only_a),
        "only_b": len(only_b),
        "different": len(different),
        "identical": len(identical),
    }
    if not quiet:
        console.print(
            f"[bold]Summary:[/bold] "
            f"only A={counts['only_a']}, only B={counts['only_b']}, "
            f"different={counts['different']}, identical={counts['identical']}"
        )
    return counts
