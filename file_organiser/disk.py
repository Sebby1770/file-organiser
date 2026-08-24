"""Disk usage map: walk a folder once, then serve a DaisyDisk-style tree.

One scandir pass accumulates directory totals, the largest files, category
weights, and empty dirs. The JSON tree is pruned (top children by size) so
the desktop UI can draw a sunburst without a million nodes.
"""

from __future__ import annotations

import heapq
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .rules import category_for_path, load_rules
from .scanner import format_size

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    "__pycache__",
    "node_modules",
    "System Volume Information",
    "$Recycle.Bin",
}
_MAX_LARGEST = 60
_MAX_STALE = 40
_MAX_EMPTY = 40
_FAN = 18
_DEPTH = 2


def candidate_roots() -> list[dict[str, str]]:
    """Folders a person actually wants to open on this machine."""
    home = Path.home()
    names = [
        ("Home", home),
        ("Downloads", home / "Downloads"),
        ("Documents", home / "Documents"),
        ("Desktop", home / "Desktop"),
        ("Pictures", home / "Pictures"),
        ("Movies", home / "Movies"),
        ("Music", home / "Music"),
    ]
    if sys.platform == "darwin":
        vols = Path("/Volumes")
        if vols.is_dir():
            try:
                for entry in vols.iterdir():
                    if entry.is_dir() and not entry.name.startswith("."):
                        names.append((entry.name, entry))
            except OSError:
                pass
    elif sys.platform.startswith("win"):
        for letter in "CDEFGH":
            drive = Path(f"{letter}:/")
            if drive.exists():
                names.append((f"{letter}:", drive))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, path in names:
        try:
            if not path.is_dir():
                continue
            key = str(path.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "path": key})
    return out


def _add_size(dir_size: dict[Path, int], dir_files: dict[Path, int], file_path: Path, root: Path, size: int) -> None:
    parent = file_path.parent
    while True:
        dir_size[parent] += size
        dir_files[parent] += 1
        if parent == root or parent.parent == parent:
            break
        parent = parent.parent


def scan_usage(
    root: Path,
    *,
    rules: dict[str, list[str]] | None = None,
    progress: Callable[[int, int], None] | None = None,
    skip_hidden: bool = True,
    stale_days: int = 365,
    stale_min_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    """Walk *root* and return a compact usage map."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")
    rules = rules or load_rules(None)
    started = time.perf_counter()
    dir_size: dict[Path, int] = defaultdict(int)
    dir_files: dict[Path, int] = defaultdict(int)
    largest: list[tuple[int, str, float, str]] = []
    stale: list[tuple[int, str, float, str]] = []
    cat_size: dict[str, int] = defaultdict(int)
    cat_files: dict[str, int] = defaultdict(int)
    empty_dirs: list[str] = []
    errors = 0
    visited = 0
    now = time.time()
    stale_age = stale_days * 86400

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        keep: list[str] = []
        for name in dirnames:
            if skip_hidden and name.startswith("."):
                continue
            if name in SKIP_DIR_NAMES:
                continue
            keep.append(name)
        dirnames[:] = keep
        dir_had_file = False
        for name in filenames:
            if skip_hidden and name.startswith("."):
                continue
            path = current / name
            try:
                st = path.stat()
            except OSError:
                errors += 1
                continue
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            size = int(st.st_size)
            mtime = float(st.st_mtime)
            visited += 1
            dir_had_file = True
            _add_size(dir_size, dir_files, path, root, size)
            cat = category_for_path(path, rules, use_smart=True)
            cat_size[cat] += size
            cat_files[cat] += 1
            item = (size, str(path), mtime, cat)
            if len(largest) < _MAX_LARGEST:
                heapq.heappush(largest, item)
            elif size > largest[0][0]:
                heapq.heapreplace(largest, item)
            if size >= stale_min_bytes and (now - mtime) >= stale_age:
                if len(stale) < _MAX_STALE:
                    heapq.heappush(stale, item)
                elif size > stale[0][0]:
                    heapq.heapreplace(stale, item)
            if progress and visited % 400 == 0:
                progress(visited, dir_size[root])
        if not dir_had_file and not dirnames and current != root:
            empty_dirs.append(str(current))
            if len(empty_dirs) > _MAX_EMPTY * 4:
                empty_dirs = empty_dirs[:_MAX_EMPTY]

    total = dir_size.get(root, 0)
    tree = _prune_tree(root, dir_size, dir_files, depth=0)
    categories = [
        {"category": k, "size": v, "files": cat_files[k], "label": format_size(v)}
        for k, v in sorted(cat_size.items(), key=lambda kv: -kv[1])
    ]
    return {
        "root": str(root),
        "name": root.name or str(root),
        "size": total,
        "label": format_size(total),
        "files": dir_files.get(root, 0),
        "visited": visited,
        "errors": errors,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "tree": tree,
        "largest": _heap_rows(largest),
        "stale": _heap_rows(stale),
        "categories": categories,
        "empty_dirs": empty_dirs[:_MAX_EMPTY],
    }


def _heap_rows(heap: list[tuple[int, str, float, str]]) -> list[dict[str, Any]]:
    rows = sorted(heap, key=lambda row: -row[0])
    return [
        {
            "path": path,
            "name": Path(path).name,
            "size": size,
            "label": format_size(size),
            "mtime": mtime,
            "category": cat,
        }
        for size, path, mtime, cat in rows
    ]


def _prune_tree(path: Path, dir_size: dict[Path, int], dir_files: dict[Path, int], depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": path.name or str(path),
        "path": str(path),
        "size": dir_size.get(path, 0),
        "files": dir_files.get(path, 0),
        "dir": True,
        "children": [],
    }
    if depth >= _DEPTH:
        return node
    kids: list[dict[str, Any]] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.name.startswith(".") or entry.name in SKIP_DIR_NAMES:
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir:
                        child_path = Path(entry.path)
                        kids.append(_prune_tree(child_path, dir_size, dir_files, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        kids.append(
                            {
                                "name": entry.name,
                                "path": entry.path,
                                "size": int(st.st_size),
                                "files": 1,
                                "dir": False,
                                "children": [],
                            }
                        )
                except OSError:
                    continue
    except OSError:
        return node
    kids.sort(key=lambda k: -int(k.get("size") or 0))
    top = kids[:_FAN]
    rest = kids[_FAN:]
    if rest:
        top.append(
            {
                "name": f"+{len(rest)} more",
                "path": "",
                "size": sum(int(k.get("size") or 0) for k in rest),
                "files": sum(int(k.get("files") or 0) for k in rest),
                "dir": True,
                "other": True,
                "children": [],
            }
        )
    node["children"] = top
    return node


def text_map(result: dict[str, Any], *, width: int = 24) -> str:
    """ASCII bar chart of the top children — CLI cousin of the sunburst."""
    tree = result.get("tree") or {}
    kids: Iterable[dict[str, Any]] = tree.get("children") or []
    total = max(1, int(result.get("size") or 1))
    lines = [f"{result.get('name')}  {result.get('label')}  ({result.get('files')} files)"]
    for child in list(kids)[:16]:
        size = int(child.get("size") or 0)
        n = max(1, int(round(width * size / total))) if size else 0
        bar = "█" * n
        lines.append(f"  {child.get('name', ''):<28} {format_size(size):>8}  {bar}")
    return "\n".join(lines)
