"""Folder scanning helpers: recursive walk, filters, size parsing."""
from __future__ import annotations

import fnmatch
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set

from .history import HISTORY_FILENAME
from .rules import category_for_path

# Internal metadata files that must never be organized / hashed as user content.
HASH_CACHE_FILENAME = ".organizer_hash_cache.json"
INTERNAL_FILENAMES: Set[str] = {HISTORY_FILENAME, HASH_CACHE_FILENAME}

# Size units for --min-size (e.g. 1K, 10M, 1.5G)
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KkMmGgTtBb]?)\s*$")
_SIZE_MULTIPLIERS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
}


def parse_size(value: str) -> int:
    """Parse a human-readable size like ``1K``, ``10M``, ``1.5G`` into bytes.

    Raises ``ValueError`` if the string is not a valid size.
    """
    if value is None:
        raise ValueError("Size value is required")
    match = _SIZE_RE.match(str(value))
    if not match:
        raise ValueError(f"Invalid size: {value!r} (examples: 1K, 10M, 1G)")
    number = float(match.group(1))
    unit = match.group(2).upper()
    return int(number * _SIZE_MULTIPLIERS[unit])


def format_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    n = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            if unit == "B":
                return f"{int(n)}B"
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{num_bytes}B"


def _matches_glob(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    """Return True if *path* matches any of *patterns* (name, relative, or segment)."""
    if not patterns:
        return False
    name = path.name
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    # Also try POSIX-style for consistent matching on Windows.
    rel_posix = path.as_posix() if path.is_absolute() else Path(rel).as_posix()
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel_posix, pattern):
            return True
        # Match path segments (e.g. exclude "node_modules")
        if pattern in path.parts:
            return True
        # Match **/pattern style against relative path parts
        if any(fnmatch.fnmatch(part, pattern) for part in path.parts):
            return True
    return False


def matches_exclude(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    """Return True if path matches any exclude glob relative to root or by name."""
    return _matches_glob(path, root, patterns)


def matches_include(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    """Return True if path should be included.

    When *patterns* is empty, every path is included. Otherwise the path must
    match at least one include glob (inverse of exclude).
    """
    if not patterns:
        return True
    return _matches_glob(path, root, patterns)


def _is_under_category(
    path: Path,
    root: Path,
    category_names: Set[str],
) -> bool:
    """True if any path component under root is a known category folder name."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    # For files: check parents; for dirs we check the dir name itself via parts
    for part in rel.parts[:-1] if path.is_file() or not path.is_dir() else rel.parts:
        if part in category_names:
            return True
    # Also: if the file lives directly in a category folder (first part)
    if rel.parts and rel.parts[0] in category_names:
        return True
    return False


def _depth_from_root(path: Path, root: Path) -> int:
    """Return nesting depth of *path* relative to *root* (0 = directly in root)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return 0
    # For a file in root: parts = (name,) → depth 0
    # For a file in sub/: parts = (sub, name) → depth 1
    return max(0, len(rel.parts) - 1)


def iter_files(
    folder: Path,
    *,
    recursive: bool = False,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    category_names: Iterable[str] | None = None,
    skip_category_folders: bool = True,
    max_depth: int | None = None,
) -> List[Path]:
    """Collect files to organize under *folder*.

    - Skips hidden files/dirs (names starting with ``.``)
    - Skips internal metadata files (history, hash cache)
    - Never follows symlinks
    - When *include* is non-empty, only paths matching at least one include
      glob are kept (applied after exclude)
    - When recursive and *skip_category_folders*, does not re-scan files already
      living under known category directories (to avoid re-organizing).
    - *max_depth*: when recursive, limit how deep to walk (0 = only top level,
      same as non-recursive; ``None`` = unlimited).
    """
    exclude = list(exclude or [])
    include = list(include or [])
    categories: Set[str] = set(category_names or [])
    results: List[Path] = []

    # max_depth 0 is equivalent to non-recursive top-level only
    if max_depth is not None and max_depth <= 0:
        recursive = False

    def consider(path: Path) -> None:
        if not path.is_file() or path.is_symlink():
            return
        if path.name.startswith("."):
            return
        if path.name in INTERNAL_FILENAMES:
            return
        if matches_exclude(path, folder, exclude):
            return
        if not matches_include(path, folder, include):
            return
        if min_size > 0:
            try:
                if path.stat().st_size < min_size:
                    return
            except OSError:
                return
        if skip_category_folders and categories and _is_under_category(path, folder, categories):
            return
        if max_depth is not None and recursive:
            if _depth_from_root(path, folder) > max_depth:
                return
        results.append(path)

    if not recursive:
        try:
            entries = list(folder.iterdir())
        except OSError:
            return []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_symlink():
                continue
            if matches_exclude(entry, folder, exclude):
                continue
            if entry.is_file():
                consider(entry)
        return results

    # Recursive walk without following symlinks
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        current = Path(dirpath)

        # Depth of this directory relative to folder (0 = folder itself)
        try:
            dir_depth = len(current.relative_to(folder).parts)
        except ValueError:
            dir_depth = 0

        # Prune hidden dirs, excluded dirs, category folders, and depth limit
        keep_dirs: List[str] = []
        for d in dirnames:
            if d.startswith("."):
                continue
            child = current / d
            if child.is_symlink():
                continue
            if matches_exclude(child, folder, exclude):
                continue
            if skip_category_folders and categories and d in categories:
                # Skip entire category subtrees to avoid re-organizing
                continue
            # Child dir depth would be dir_depth + 1; files inside it have
            # depth dir_depth + 1. Allow walking into dirs whose files would
            # still be within max_depth.
            if max_depth is not None and (dir_depth + 1) > max_depth:
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for name in filenames:
            consider(current / name)

    return results


def scan_folder(
    folder: Path,
    rules: Dict[str, List[str]],
    *,
    recursive: bool = False,
    exclude: Sequence[str] | None = None,
    include: Sequence[str] | None = None,
    min_size: int = 0,
    use_mime: bool = False,
    max_depth: int | None = None,
) -> Dict[str, List[Path]]:
    """Group scannable files by target category."""
    category_names = set(rules.keys()) | {"Other"}
    files = iter_files(
        folder,
        recursive=recursive,
        exclude=exclude,
        include=include,
        min_size=min_size,
        category_names=category_names,
        skip_category_folders=True,
        max_depth=max_depth,
    )
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for path in files:
        category = category_for_path(path, rules, use_mime=use_mime)
        grouped[category].append(path)
    return grouped
