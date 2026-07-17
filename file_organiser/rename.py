"""Bulk rename files with patterns or slugify."""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from rich.console import Console
from rich.table import Table

from .history import HistoryManager
from .scanner import INTERNAL_FILENAMES, format_size, iter_files

# Tokens in --pattern: {n}, {n:04d}, {name}, {stem}, {ext}, {ext_no_dot}
# Match longest keywords first (ext_no_dot before ext).
_TOKEN_RE = re.compile(
    r"\{(n(?::(\d+)d)?|name|stem|ext_no_dot|ext)\}"
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase and replace non-alphanumeric runs with ``-``; strip edges."""
    stem = Path(name).stem
    ext = Path(name).suffix
    slug = _SLUG_RE.sub("-", stem.lower()).strip("-")
    if not slug:
        slug = "file"
    return f"{slug}{ext.lower()}" if ext else slug


def apply_pattern(template: str, path: Path, index: int) -> str:
    """Render a rename pattern for *path* with 1-based *index*.

    Supported tokens:
      ``{n}`` / ``{n:04d}`` — sequence number
      ``{name}`` — full original filename
      ``{stem}`` — filename without extension
      ``{ext}`` — extension including dot (or empty)
      ``{ext_no_dot}`` — extension without leading dot
    """

    def repl(m: re.Match[str]) -> str:
        token = m.group(1)
        if token == "n" or token.startswith("n:"):
            width = m.group(2)
            if width:
                return f"{index:0{int(width)}d}"
            return str(index)
        if token == "name":
            return path.name
        if token == "stem":
            return path.stem
        if token == "ext_no_dot":
            return path.suffix[1:] if path.suffix.startswith(".") else path.suffix
        if token == "ext":
            return path.suffix
        return m.group(0)

    return _TOKEN_RE.sub(repl, template)


def plan_renames(
    folder: Path,
    *,
    pattern: Optional[str] = None,
    slug: bool = False,
    match: Optional[str] = None,
    recursive: bool = False,
    max_depth: int | None = None,
) -> List[Tuple[Path, Path]]:
    """Plan (src, dest) rename pairs. Does not touch disk beyond scanning.

    Exactly one of *pattern* or *slug* should be set (caller validates).
    """
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=None,
        include=[match] if match else None,
        min_size=0,
        category_names=None,
        skip_category_folders=False,
        max_depth=max_depth,
    )
    # Stable order by path
    files = sorted(files, key=lambda p: str(p).lower())

    if match:
        files = [p for p in files if fnmatch.fnmatch(p.name, match)]

    pairs: List[Tuple[Path, Path]] = []
    planned_names: set[Path] = set()

    for i, src in enumerate(files, start=1):
        if src.name in INTERNAL_FILENAMES:
            continue
        if slug:
            new_name = slugify(src.name)
        else:
            assert pattern is not None
            new_name = apply_pattern(pattern, src, i)

        # Safety: no path separators in new name
        new_name = new_name.replace("/", "_").replace("\\", "_")
        if not new_name or new_name in (".", ".."):
            continue

        dest = src.parent / new_name
        if dest == src:
            continue

        # Collision avoidance among planned + existing
        if dest.exists() or dest in planned_names:
            stem = Path(new_name).stem
            suffix = Path(new_name).suffix
            counter = 1
            while True:
                candidate = src.parent / f"{stem}_{counter}{suffix}"
                if not candidate.exists() and candidate not in planned_names:
                    dest = candidate
                    break
                counter += 1

        planned_names.add(dest)
        pairs.append((src, dest))

    return pairs


def rename_files(
    folder: Path,
    console: Console,
    *,
    pattern: Optional[str] = None,
    slug: bool = False,
    match: Optional[str] = None,
    apply: bool = False,
    recursive: bool = False,
    max_depth: int | None = None,
    quiet: bool = False,
) -> int:
    """Bulk rename files. Dry-run by default; *apply* executes.

    Records renames in history (mode=``rename``) when applied so undo can reverse.
    Returns number of renames planned/performed.
    """
    if not folder.exists() or not folder.is_dir():
        console.print(f"[red]Error:[/red] '{folder}' is not a valid directory.")
        return 0

    if not pattern and not slug:
        console.print("[red]Error:[/red] provide --pattern or --slug.")
        return 0
    if pattern and slug:
        console.print("[red]Error:[/red] use either --pattern or --slug, not both.")
        return 0

    pairs = plan_renames(
        folder,
        pattern=pattern,
        slug=slug,
        match=match,
        recursive=recursive,
        max_depth=max_depth,
    )

    if not pairs:
        console.print(f"[yellow]No files to rename in[/yellow] {folder}")
        return 0

    table = Table(
        title=f"{'Rename (apply)' if apply else 'Rename (dry-run)'}: {folder}",
        header_style="bold cyan",
    )
    table.add_column("From", style="white")
    table.add_column("To", style="green")

    for src, dest in pairs:
        try:
            rel_src = str(src.relative_to(folder))
            rel_dest = str(dest.relative_to(folder))
        except ValueError:
            rel_src, rel_dest = str(src), str(dest)
        table.add_row(rel_src, rel_dest)

    console.print(table)

    if not apply:
        if not quiet:
            console.print(
                f"[yellow]Dry run:[/yellow] would rename {len(pairs)} file(s). "
                f"Re-run with [bold]--apply[/bold] to execute."
            )
        return len(pairs)

    history_moves: List[Tuple[Path, Path]] = []
    errors: List[str] = []
    success = 0

    for src, dest in pairs:
        try:
            if not src.exists():
                errors.append(f"Missing: {src}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
            # History: (current, original) for undo
            history_moves.append((dest, src))
            success += 1
        except OSError as e:
            errors.append(f"{src.name}: {e}")

    if history_moves:
        HistoryManager(folder).save(history_moves, mode="rename")

    if not quiet:
        console.print(
            f"[green]✓[/green] Renamed {success} file(s). "
            f"Run [bold]undo[/bold] to revert."
        )
    if errors and not quiet:
        console.print(f"[red]Encountered {len(errors)} error(s):[/red]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")

    return success
