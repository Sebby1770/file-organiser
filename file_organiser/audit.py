"""Read-only clutter audit: loose files, copy-like names, and rule clashes.

Nothing is moved or deleted. The score is a deterministic reading of the
names in one folder, not a judgement that a file is safe to remove.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .disk import CACHE_DIR_NAMES, SKIP_DIR_NAMES
from .rules import OTHER_CATEGORY, category_for_extension
from .scanner import INTERNAL_FILENAMES

MAX_FILES = 20000
MAX_LISTED = 40
STALE_LOOSE_DAYS = 180

_TRAILING_MARKER = re.compile(
    r"[\s._-]*(copy|final|draft)(?:[\s._-]*\d+)?$",
    re.IGNORECASE,
)
_TRAILING_NUMBER = re.compile(r"\s*\(\d+\)$")
_TRAILING_COPY_PAREN = re.compile(r"\s*\(copy\)$", re.IGNORECASE)
_SPACE_RUN = re.compile(r"[\s._-]+")


def normalize_stem(filename: str) -> str:
    """Collapse 'Report (1)', 'Report copy', and 'Report final' to one stem."""
    stem = Path(filename).stem.lower()
    previous = None
    while stem and stem != previous:
        previous = stem
        stem = _TRAILING_NUMBER.sub("", stem)
        stem = _TRAILING_COPY_PAREN.sub("", stem)
        stem = _TRAILING_MARKER.sub("", stem)
        stem = stem.strip(" ._-")
    stem = _SPACE_RUN.sub(" ", stem).strip()
    return stem or Path(filename).stem.lower()


def extension_conflicts(rules: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Extensions claimed by more than one category, in first-seen order."""
    owners: dict[str, list[str]] = {}
    for category, extensions in rules.items():
        for raw in extensions:
            ext = str(raw).lower().strip()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = f".{ext}"
            bucket = owners.setdefault(ext, [])
            if category not in bucket:
                bucket.append(category)
    return [
        {"extension": ext, "categories": categories}
        for ext, categories in owners.items()
        if len(categories) > 1
    ]


def name_twins(records: list[dict[str, Any]], *, limit: int = MAX_LISTED) -> list[dict[str, Any]]:
    """Group files that share a normalised stem and the same suffix."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        key = (normalize_stem(record["name"]), record["suffix"])
        if not key[0]:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)
    twins: list[dict[str, Any]] = []
    for key in order:
        members = groups[key]
        if len(members) < 2:
            continue
        twins.append(
            {
                "stem": key[0],
                "suffix": key[1],
                "count": len(members),
                "files": [
                    {"name": item["name"], "path": item["path"], "rel": item["rel"]}
                    for item in members[:8]
                ],
            }
        )
        if len(twins) >= limit:
            break
    return twins


def clutter_score(
    *,
    files: int,
    loose_count: int,
    twin_groups: int,
    other_count: int,
    empty_count: int,
    stale_loose: int = 0,
) -> int:
    """0 is messy, 100 is tidy. An empty folder scores 100."""
    if files <= 0:
        return 100
    loose_penalty = min(40, loose_count * 4)
    twin_penalty = min(30, twin_groups * 6)
    other_penalty = min(20, round((other_count / files) * 20))
    empty_penalty = min(10, empty_count)
    stale_penalty = min(10, stale_loose * 2)
    score = 100 - loose_penalty - twin_penalty - other_penalty - empty_penalty - stale_penalty
    return max(0, min(100, score))


def grade_for(score: int) -> str:
    if score >= 80:
        return "Tidy"
    if score >= 50:
        return "Mixed"
    return "Cluttered"


def tidy_preview(folder: Path, rules: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Dry-run moves for loose files that already match a category.

    Only the folder root is considered. Nothing is created or moved.
    ``Other`` stays where it is.
    """
    from .organizer import plan_moves

    root = folder.expanduser().resolve()
    pairs, _skips = plan_moves(root, rules, max_depth=0, on_conflict="rename")
    moves: list[dict[str, Any]] = []
    for src, dest in pairs:
        try:
            if src.parent.resolve() != root:
                continue
            rel = dest.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        category = rel.split("/", 1)[0]
        if category == OTHER_CATEGORY:
            continue
        moves.append(
            {
                "name": src.name,
                "src": str(src),
                "dest": str(dest),
                "rel_dest": rel,
                "category": category,
            }
        )
    return moves


def audit_folder(
    folder: Path,
    rules: dict[str, list[str]] | None = None,
    *,
    max_files: int = MAX_FILES,
    preview: bool = False,
) -> dict[str, Any]:
    """Walk ``folder`` without following links and summarise clutter."""
    root = folder.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")
    rules = rules or {}
    records: list[dict[str, Any]] = []
    truncated = False
    now = time.time()
    stale_after = STALE_LOOSE_DAYS * 86400

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in sorted(dirnames):
            if name.startswith(".") or name in CACHE_DIR_NAMES or name in SKIP_DIR_NAMES:
                continue
            child = current / name
            if child.is_symlink():
                continue
            kept.append(name)
        dirnames[:] = kept
        if len(records) >= max_files:
            truncated = True
            dirnames[:] = []
            break
        for name in sorted(filenames):
            if len(records) >= max_files:
                truncated = True
                break
            if name.startswith(".") or name in INTERNAL_FILENAMES:
                continue
            path = current / name
            if path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            records.append(
                {
                    "name": name,
                    "path": str(path),
                    "rel": path.relative_to(root).as_posix(),
                    "size": int(stat.st_size),
                    "suffix": suffix,
                    "loose": path.parent == root,
                    "category": category_for_extension(suffix, rules),
                    "mtime": float(stat.st_mtime),
                }
            )
        if truncated:
            dirnames[:] = []

    loose = [item for item in records if item["loose"]]
    twins = name_twins(records)
    conflicts = extension_conflicts(rules)
    other_count = sum(1 for item in records if item["category"] == OTHER_CATEGORY)
    empty_count = sum(1 for item in records if item["size"] == 0)
    stale_loose = sum(
        1 for item in loose if now - item["mtime"] >= stale_after
    )
    score = clutter_score(
        files=len(records),
        loose_count=len(loose),
        twin_groups=len(twins),
        other_count=other_count,
        empty_count=empty_count,
        stale_loose=stale_loose,
    )
    movable = [item for item in loose if item["category"] != OTHER_CATEGORY]
    report = {
        "ok": True,
        "root": str(root),
        "files": len(records),
        "truncated": truncated,
        "score": score,
        "grade": grade_for(score),
        "loose_count": len(loose),
        "movable_count": len(movable),
        "other_count": other_count,
        "empty_count": empty_count,
        "stale_loose": stale_loose,
        "loose": [_public_file(item) for item in loose[:MAX_LISTED]],
        "movable": [_public_file(item) for item in movable[:MAX_LISTED]],
        "twins": twins,
        "conflicts": conflicts,
    }
    report["preview"] = tidy_preview(root, rules) if preview else []
    report["headline"] = _headline(report)
    return report


def format_audit(report: dict[str, Any]) -> str:
    """Plain-text rendering of :func:`audit_folder`."""
    cap = " (first files only)" if report.get("truncated") else ""
    lines = [
        f"Clutter score: {report['score']}/100 ({report['grade']})",
        report["headline"],
        f"Files seen: {report['files']}{cap}",
        f"Loose in this folder: {report['loose_count']}",
        f"Loose files with a category: {report['movable_count']}",
        f"Name-twin groups: {len(report['twins'])}",
        f"Rule conflicts: {len(report['conflicts'])}",
        f"Loose files older than {STALE_LOOSE_DAYS} days: {report.get('stale_loose', 0)}",
    ]
    if report["loose"]:
        lines.append("Loose files:")
        lines.extend(f"  {item['rel']} -> {item['category']}" for item in report["loose"][:12])
    if report["twins"]:
        lines.append("Copy-like names:")
        for group in report["twins"][:8]:
            names = ", ".join(item["rel"] for item in group["files"])
            lines.append(f"  {group['count']}x {group['stem']}{group['suffix']}: {names}")
    if report["conflicts"]:
        lines.append("Extensions in more than one category:")
        for clash in report["conflicts"]:
            lines.append(f"  {clash['extension']} -> {', '.join(clash['categories'])}")
    if report.get("preview"):
        lines.append("Tidy preview (not applied):")
        lines.extend(f"  {item['name']} -> {item['rel_dest']}" for item in report["preview"][:12])
    lines.append("This audit does not delete or move anything.")
    return "\n".join(lines)


def _public_file(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item["name"],
        "path": item["path"],
        "rel": item["rel"],
        "size": item["size"],
        "category": item["category"],
    }


def _headline(report: dict[str, Any]) -> str:
    if report["files"] == 0:
        return "No files to tidy in this folder."
    loose = report["loose_count"]
    twins = len(report["twins"])
    loose_text = (
        f"{loose} loose file{'' if loose == 1 else 's'} in the folder root"
        if loose
        else "no loose files in the folder root"
    )
    twin_text = (
        f"{twins} copy-name group{'' if twins == 1 else 's'}"
        if twins
        else "no copy-name groups"
    )
    text = f"Clutter score {report['score']}/100. Found {loose_text} and {twin_text}."
    clashes = len(report["conflicts"])
    if clashes:
        text += f" Rules overlap on {clashes} extension{'' if clashes == 1 else 's'}."
    if report.get("stale_loose"):
        text += f" {report['stale_loose']} loose file(s) are older than {STALE_LOOSE_DAYS} days."
    if report["truncated"]:
        text += " The score uses the first files only."
    return text
