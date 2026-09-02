"""Disk usage map: one walk, then a sunburst/treemap the desktop app can zoom."""

from __future__ import annotations

import heapq
import os
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .clean import DEFAULT_JUNK_PATTERNS, matches_junk_name
from .rules import category_for_path, load_rules
from .scanner import format_size

SKIP_DIR_NAMES = {
    "System Volume Information",
    "$Recycle.Bin",
}
CACHE_DIR_NAMES = {
    "node_modules",
    "__pycache__",
    ".cache",
    ".gradle",
    ".npm",
    ".yarn",
    "DerivedData",
    "bower_components",
    ".tox",
    ".venv",
    "venv",
    "Caches",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".parcel-cache",
    ".next",
    ".turbo",
    ".ccache",
    "pip-wheel-metadata",
}
INSTALLER_EXTS = {".dmg", ".pkg", ".msi", ".exe", ".deb", ".rpm", ".appimage"}
_MAX_LARGEST = 80
_MAX_STALE = 50
_MAX_EMPTY = 50
_MAX_HINTS = 80
_FAN = 20
_DEPTH = 3


@dataclass
class ScanMaps:
    dir_size: dict[Path, int] = field(default_factory=dict)
    dir_files: dict[Path, int] = field(default_factory=dict)


def candidate_roots() -> list[dict[str, str]]:
    """Folders a person actually wants to open on this machine."""
    home = Path.home()
    names = [
        ("Home folder", home),
        ("Downloads", home / "Downloads"),
        ("Documents", home / "Documents"),
        ("Desktop", home / "Desktop"),
        ("Pictures", home / "Pictures"),
        ("Movies", home / "Movies"),
        ("Music", home / "Music"),
    ]
    if sys.platform == "darwin":
        caches = home / "Library" / "Caches"
        if caches.is_dir():
            names.append(("App caches", caches))
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


def volume_stats(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {}
    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "total_label": format_size(usage.total),
        "used_label": format_size(usage.used),
        "free_label": format_size(usage.free),
        "pct": round(100.0 * usage.used / usage.total, 1) if usage.total else 0,
    }


def _is_cache_path(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part in CACHE_DIR_NAMES for part in rel.parts)


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
    cancel: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], ScanMaps]:
    """Walk *root* and return (JSON-safe map, internal dir totals for zoom)."""
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
    cache_bytes = 0
    cache_files = 0
    empty_dirs: list[str] = []
    junk_rows: list[dict[str, Any]] = []
    empty_files: list[dict[str, Any]] = []
    installer_rows: list[dict[str, Any]] = []
    errors = 0
    visited = 0
    now = time.time()
    stale_age = stale_days * 86400

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if cancel and cancel():
            break
        current = Path(dirpath)
        keep: list[str] = []
        for name in dirnames:
            if skip_hidden and name.startswith(".") and name not in CACHE_DIR_NAMES:
                continue
            if name in SKIP_DIR_NAMES:
                continue
            keep.append(name)
        dirnames[:] = keep
        dir_had_file = False
        for name in filenames:
            is_junk = matches_junk_name(name, DEFAULT_JUNK_PATTERNS) or name.endswith(".pyc")
            if skip_hidden and name.startswith(".") and not is_junk:
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
            if _is_cache_path(path, root):
                cache_bytes += size
                cache_files += 1
            if is_junk and len(junk_rows) < _MAX_HINTS:
                junk_rows.append(
                    {
                        "path": str(path),
                        "name": name,
                        "size": size,
                        "label": format_size(size),
                        "reason": "Junk leftover (.DS_Store, Thumbs.db, temp, bytecode).",
                    }
                )
            if size == 0 and len(empty_files) < _MAX_HINTS:
                empty_files.append({"path": str(path), "name": name, "size": 0, "label": "0B"})
            suffix = path.suffix.lower()
            if suffix in INSTALLER_EXTS and len(installer_rows) < _MAX_HINTS:
                installer_rows.append(
                    {
                        "path": str(path),
                        "name": name,
                        "size": size,
                        "label": format_size(size),
                        "age_days": round((now - mtime) / 86400, 1),
                    }
                )
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
            if progress and visited % 250 == 0:
                progress(visited, dir_size[root])
        if not dir_had_file and not dirnames and current != root:
            empty_dirs.append(str(current))
            if len(empty_dirs) > _MAX_EMPTY * 4:
                empty_dirs = empty_dirs[:_MAX_EMPTY]

    total = dir_size.get(root, 0)
    tree = prune_tree(root, dir_size, dir_files, depth=0, rules=rules)
    stale_rows = _heap_rows(stale)
    stale_bytes = sum(row["size"] for row in stale_rows)
    waste = cache_bytes + stale_bytes
    categories = [
        {"category": k, "size": v, "files": cat_files[k], "label": format_size(v)}
        for k, v in sorted(cat_size.items(), key=lambda kv: -kv[1])
    ]
    cache_dirs = _outermost_cache_dirs(dir_size, dir_files, root)
    result = {
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
        "stale": stale_rows,
        "categories": categories,
        "empty_dirs": empty_dirs[:_MAX_EMPTY],
        "cache_dirs": cache_dirs,
        "junk": junk_rows[:_MAX_HINTS],
        "empty_files": empty_files[:_MAX_HINTS],
        "installers": installer_rows[:_MAX_HINTS],
        "volume": volume_stats(root),
        "waste": {
            "caches": cache_bytes,
            "caches_label": format_size(cache_bytes),
            "cache_files": cache_files,
            "stale": stale_bytes,
            "stale_label": format_size(stale_bytes),
            "empty": len(empty_dirs[:_MAX_EMPTY]),
            "total": waste,
            "total_label": format_size(waste),
        },
    }
    return result, ScanMaps(dir_size=dict(dir_size), dir_files=dict(dir_files))


def _outermost_cache_dirs(
    dir_size: dict[Path, int],
    dir_files: dict[Path, int],
    root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, size in dir_size.items():
        if path == root or path.name not in CACHE_DIR_NAMES:
            continue
        nested = False
        parent = path.parent
        while parent != path:
            if parent.name in CACHE_DIR_NAMES:
                nested = True
                break
            if parent == root or parent.parent == parent:
                break
            parent = parent.parent
        if nested:
            continue
        rows.append(
            {
                "path": str(path),
                "name": path.name,
                "size": int(size),
                "files": int(dir_files.get(path, 0)),
                "label": format_size(int(size)),
            }
        )
    rows.sort(key=lambda r: -int(r["size"]))
    return rows[:_MAX_HINTS]


def prune_tree(
    path: Path,
    dir_size: dict[Path, int],
    dir_files: dict[Path, int],
    depth: int,
    fan: int = _FAN,
    max_depth: int = _DEPTH,
    rules: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    rules = rules or load_rules(None)
    node: dict[str, Any] = {
        "name": path.name or str(path),
        "path": str(path),
        "size": dir_size.get(path, 0),
        "files": dir_files.get(path, 0),
        "dir": True,
        "children": [],
    }
    if depth >= max_depth:
        return node
    kids: list[dict[str, Any]] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.name in SKIP_DIR_NAMES:
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir:
                        child_path = Path(entry.path)
                        kids.append(
                            prune_tree(child_path, dir_size, dir_files, depth + 1, fan, max_depth, rules)
                        )
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
                                "category": category_for_path(Path(entry.path), rules, use_smart=True),
                            }
                        )
                except OSError:
                    continue
    except OSError:
        return node
    kids.sort(key=lambda k: -int(k.get("size") or 0))
    top = kids[:fan]
    rest = kids[fan:]
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


def text_map(result: dict[str, Any], *, width: int = 24) -> str:
    """ASCII bar chart of the top children — CLI cousin of the sunburst."""
    tree = result.get("tree") or {}
    kids: Iterable[dict[str, Any]] = tree.get("children") or []
    total = max(1, int(result.get("size") or 1))
    lines = [f"{result.get('name')}  {result.get('label')}  ({result.get('files')} files)"]
    vol = result.get("volume") or {}
    if vol.get("free_label"):
        lines.append(f"  volume {vol.get('used_label')} used · {vol.get('free_label')} free")
    waste = result.get("waste") or {}
    if waste.get("total"):
        lines.append(f"  reclaimable ~ {waste.get('total_label')} (caches + stale)")
    for child in list(kids)[:16]:
        size = int(child.get("size") or 0)
        n = max(1, int(round(width * size / total))) if size else 0
        bar = "█" * n
        lines.append(f"  {child.get('name', ''):<28} {format_size(size):>8}  {bar}")
    return "\n".join(lines)
