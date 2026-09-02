"""Tell a person what is safe to delete, what to review, and what to keep."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .clean import DEFAULT_JUNK_PATTERNS, matches_junk_name
from .disk import CACHE_DIR_NAMES, INSTALLER_EXTS
from .safety import dangerous_target
from .scanner import format_size

_MAX_BUCKET = 80

# Folders that are someone's life, not leftover software.
KEEP_DIR_NAMES = {
    "Documents",
    "Pictures",
    "Photos",
    "Music",
    "Movies",
    "Desktop",
    "Applications",
    "Public",
    "Sites",
    "Books",
    "Creative Cloud Files",
    "iCloud Drive",
    "Mobile Documents",
    "Mail",
    "Messages",
    "Downloads",  # mixed; contents are classified, the folder itself is kept
}

PROTECTED_PARTS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    "Keychains",
    "Mail",
    "Messages",
    "Photos Library.photoslibrary",
    "Mobile Documents",
}

PROTECTED_SUFFIXES = (
    ".photoslibrary",
    ".keychain-db",
    ".keychain",
)

KEEP_CATEGORIES = {
    "Images",
    "Documents",
    "Ebooks",
    "Audio",
    "Videos",
    "Spreadsheets",
    "Presentations",
    "Design",
    "Code",
}

REVIEW_BUILD_NAMES = {"build", "dist", "out", "target", "DerivedData"}

USER_LIBRARY_HINTS = ("Pictures", "Documents", "Music", "Movies", "Photos", "Desktop")


def is_protected(path: Path) -> bool:
    """True if this path must never be recommended for deletion."""
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path
    if dangerous_target(resolved):
        return True
    name = resolved.name
    if name in PROTECTED_PARTS:
        return True
    if any(name.endswith(suffix) for suffix in PROTECTED_SUFFIXES):
        return True
    parts = set(resolved.parts)
    if parts & PROTECTED_PARTS:
        return True
    if name.startswith("id_") and resolved.parent.name == ".ssh":
        return True
    return False


def classify_path(
    path: Path,
    *,
    size: int = 0,
    age_days: float | None = None,
    category: str = "",
    root: Path | None = None,
    is_dir: bool = False,
) -> tuple[str, str, str]:
    """Return ``(bucket, reason, kind)`` for a single path.

    bucket is ``delete``, ``review``, or ``keep``.
    """
    path = Path(path)
    name = path.name
    if is_protected(path):
        return ("keep", "Keys, mail, photos, or a whole home folder — leave it.", "protected")

    if name in CACHE_DIR_NAMES or (is_dir and name in CACHE_DIR_NAMES) or any(
        part in CACHE_DIR_NAMES for part in path.parts
    ):
        return ("delete", "Regenerable cache. Apps and installs will recreate this.", "cache")

    if is_dir and name in REVIEW_BUILD_NAMES:
        return ("review", "Looks like build output. Confirm it is not a folder you named yourself.", "build")

    if is_dir and name in KEEP_DIR_NAMES:
        return ("keep", "Your files live here.", "library")

    if is_dir and name in {".git", ".hg", ".svn"}:
        return ("keep", "Version control. Deleting this orphans the project.", "vcs")

    if matches_junk_name(name, DEFAULT_JUNK_PATTERNS) or name.endswith(".pyc"):
        return ("delete", "Junk leftover (.DS_Store, Thumbs.db, temp, bytecode).", "junk")

    suffix = path.suffix.lower()
    if suffix in INSTALLER_EXTS:
        in_downloads = "Downloads" in path.parts
        if in_downloads and age_days is not None and age_days >= 45:
            return (
                "review",
                f"Old installer in Downloads ({int(age_days)} days). Confirm the app is installed before trashing it.",
                "installer",
            )
        if in_downloads:
            return ("review", "Installer in Downloads. Keep it if you still need to run it.", "installer")
        return ("review", "Installer package. Only trash it if the app is already installed.", "installer")

    if size == 0 and not is_dir:
        return ("review", "Empty file. It may still be a marker used by an app or project.", "empty")

    if is_dir and name.lower() in {"node_modules", "__pycache__", "caches"}:
        return ("delete", "Regenerable cache. Apps and installs will recreate this.", "cache")

    in_user_library = any(part in USER_LIBRARY_HINTS for part in path.parts)
    if in_user_library and category in KEEP_CATEGORIES:
        return ("keep", f"{category} in {path.parent.name or 'your library'} — this is yours.", "library")

    if category in KEEP_CATEGORIES and in_user_library:
        return ("keep", "Personal library file.", "library")

    if age_days is not None and age_days >= 365 and size >= 8 * 1024 * 1024:
        if in_user_library:
            return ("keep", "Old, but it sits in a personal library. You should decide.", "stale")
        return (
            "review",
            f"Large and untouched for {int(age_days)} days. Open it before trashing.",
            "stale",
        )

    if is_dir:
        return ("review", "Folder taking space. Peek inside before you throw it away.", "folder")

    if category in {"Archives", "Executables"}:
        return ("review", "Archive or program. Keep if you still use it.", "archive")

    if category in KEEP_CATEGORIES:
        return ("keep", f"{category}. We do not auto-trash your work.", "library")

    return ("review", "Not junk, not obviously personal. Look before you trash it.", "unknown")


def _item(
    path: str,
    *,
    size: int,
    bucket: str,
    reason: str,
    kind: str,
    confidence: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p = Path(path)
    row = {
        "path": path,
        "name": p.name or path,
        "size": int(size),
        "label": format_size(int(size)),
        "bucket": bucket,
        "reason": reason,
        "kind": kind,
        "confidence": confidence,
        "dir": extra.get("dir") if extra else False,
    }
    if extra:
        for key, value in extra.items():
            row.setdefault(key, value)
    return row


def _confidence(kind: str, bucket: str) -> str:
    if bucket == "keep":
        return "high"
    if kind in {"cache", "junk", "empty"}:
        return "high"
    if kind == "installer" and bucket == "delete":
        return "medium"
    if bucket == "review":
        return "medium"
    return "low"


def _seen_add(seen: set[str], path: str) -> bool:
    key = str(path)
    if key in seen:
        return False
    seen.add(key)
    return True


def build_advice(result: dict[str, Any] | None, *, dupe_extras: Iterable[str] | None = None) -> dict[str, Any]:
    """Turn a disk scan into delete / review / keep lists."""
    result = result or {}
    root = Path(str(result.get("root") or ""))
    delete: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []
    seen: set[str] = set()

    def place(row: dict[str, Any]) -> None:
        if not _seen_add(seen, row["path"]):
            return
        if is_protected(Path(row["path"])):
            row["bucket"] = "keep"
            row["reason"] = "Protected — keys, photos, mail, or a whole home folder."
            row["kind"] = "protected"
            row["confidence"] = "high"
            keep.append(row)
            return
        if row["bucket"] == "delete":
            delete.append(row)
        elif row["bucket"] == "keep":
            keep.append(row)
        else:
            review.append(row)

    for cache in result.get("cache_dirs") or []:
        path = cache.get("path") or ""
        if not path:
            continue
        bucket, reason, kind = classify_path(Path(path), size=int(cache.get("size") or 0), is_dir=True, root=root)
        place(
            _item(
                path,
                size=int(cache.get("size") or 0),
                bucket=bucket,
                reason=reason,
                kind=kind,
                confidence=_confidence(kind, bucket),
                extra={"dir": True, "files": cache.get("files") or 0},
            )
        )

    for junk in result.get("junk") or []:
        path = junk.get("path") or ""
        if not path:
            continue
        bucket, reason, kind = classify_path(Path(path), size=int(junk.get("size") or 0), root=root)
        place(
            _item(
                path,
                size=int(junk.get("size") or 0),
                bucket=bucket,
                reason=junk.get("reason") or reason,
                kind="junk",
                confidence="high",
            )
        )

    for empty in result.get("empty_files") or []:
        path = empty.get("path") or ""
        if not path:
            continue
        place(
            _item(
                path,
                size=0,
                bucket="delete",
                reason="Empty file. Nothing in it.",
                kind="empty",
                confidence="high",
            )
        )

    for inst in result.get("installers") or []:
        path = inst.get("path") or ""
        if not path:
            continue
        age = inst.get("age_days")
        bucket, reason, kind = classify_path(
            Path(path),
            size=int(inst.get("size") or 0),
            age_days=float(age) if age is not None else None,
            root=root,
        )
        place(
            _item(
                path,
                size=int(inst.get("size") or 0),
                bucket=bucket,
                reason=reason,
                kind="installer",
                confidence=_confidence("installer", bucket),
                extra={"age_days": age},
            )
        )

    for stale in result.get("stale") or []:
        path = stale.get("path") or ""
        if not path:
            continue
        mtime = stale.get("mtime")
        age_days = None
        if mtime:
            import time as _time

            age_days = max(0.0, (_time.time() - float(mtime)) / 86400)
        bucket, reason, kind = classify_path(
            Path(path),
            size=int(stale.get("size") or 0),
            age_days=age_days,
            category=str(stale.get("category") or ""),
            root=root,
        )
        if bucket == "delete":
            bucket, reason, kind = "review", reason, "stale"
        place(
            _item(
                path,
                size=int(stale.get("size") or 0),
                bucket=bucket,
                reason=reason,
                kind=kind,
                confidence=_confidence(kind, bucket),
            )
        )

    for empty_dir in result.get("empty_dirs") or []:
        place(
            _item(
                str(empty_dir),
                size=0,
                bucket="delete",
                reason="Empty folder.",
                kind="empty",
                confidence="high",
                extra={"dir": True},
            )
        )

    for extra in dupe_extras or []:
        place(
            _item(
                str(extra),
                size=0,
                bucket="review",
                reason="Duplicate copy. Keep one, trash the extras.",
                kind="duplicate",
                confidence="medium",
            )
        )

    if root and str(root) not in {".", ""}:
        for name in (".ssh", ".gnupg"):
            candidate = root / name
            if candidate.is_dir():
                place(
                    _item(
                        str(candidate),
                        size=0,
                        bucket="keep",
                        reason="Keys. Protected.",
                        kind="protected",
                        confidence="high",
                        extra={"dir": True},
                    )
                )

    tree = result.get("tree") or {}
    for child in tree.get("children") or []:
        path = child.get("path") or ""
        if not path or child.get("other"):
            continue
        name = child.get("name") or Path(path).name
        is_dir = bool(child.get("dir"))
        bucket, reason, kind = classify_path(
            Path(path),
            size=int(child.get("size") or 0),
            category=str(child.get("category") or ""),
            root=root,
            is_dir=is_dir,
        )
        if bucket != "keep" and name not in KEEP_DIR_NAMES and kind not in {"protected", "library", "vcs"}:
            continue
        if bucket == "keep":
            place(
                _item(
                    path,
                    size=int(child.get("size") or 0),
                    bucket="keep",
                    reason=reason,
                    kind=kind,
                    confidence="high",
                    extra={"dir": is_dir},
                )
            )

    for big in result.get("largest") or []:
        path = big.get("path") or ""
        if not path or path in seen:
            continue
        bucket, reason, kind = classify_path(
            Path(path),
            size=int(big.get("size") or 0),
            category=str(big.get("category") or ""),
            root=root,
        )
        if bucket == "keep":
            place(
                _item(
                    path,
                    size=int(big.get("size") or 0),
                    bucket="keep",
                    reason=reason,
                    kind=kind,
                    confidence="high",
                )
            )
        elif bucket == "review":
            place(
                _item(
                    path,
                    size=int(big.get("size") or 0),
                    bucket="review",
                    reason=reason or "One of the largest things on disk.",
                    kind=kind or "large",
                    confidence="medium",
                )
            )

    delete.sort(key=lambda r: -int(r["size"]))
    review.sort(key=lambda r: -int(r["size"]))
    keep.sort(key=lambda r: -int(r["size"]))
    delete = delete[:_MAX_BUCKET]
    review = review[:_MAX_BUCKET]
    keep = keep[:_MAX_BUCKET]

    delete_bytes = sum(int(r["size"]) for r in delete)
    review_bytes = sum(int(r["size"]) for r in review)
    keep_bytes = sum(int(r["size"]) for r in keep)
    headline = _headline(delete_bytes, review_bytes, keep_bytes, len(delete), len(review))
    return {
        "delete": delete,
        "review": review,
        "keep": keep,
        "summary": {
            "delete_bytes": delete_bytes,
            "delete_label": format_size(delete_bytes),
            "delete_n": len(delete),
            "review_bytes": review_bytes,
            "review_label": format_size(review_bytes),
            "review_n": len(review),
            "keep_bytes": keep_bytes,
            "keep_label": format_size(keep_bytes),
            "keep_n": len(keep),
            "headline": headline,
            "root": str(result.get("root") or ""),
        },
    }


def _headline(delete_b: int, review_b: int, keep_b: int, delete_n: int, review_n: int) -> str:
    if delete_b == 0 and review_b == 0:
        return "This looks tidy. Nothing obvious to throw away."
    if delete_b and review_b:
        return (
            f"Safe to delete about {format_size(delete_b)} "
            f"({delete_n} items). Check {format_size(review_b)} more before you touch it."
        )
    if delete_b:
        return f"Safe to delete about {format_size(delete_b)} across {delete_n} items. We will not trash your photos or documents."
    return f"Nothing is an obvious delete. {format_size(review_b)} is worth a look ({review_n} items)."


def format_advice(advice: dict[str, Any]) -> str:
    """Plain-text report for the CLI."""
    summary = advice.get("summary") or {}
    lines = [
        summary.get("headline") or "Advice",
        f"  delete  {summary.get('delete_label', '0B')}  ({summary.get('delete_n', 0)} items)",
        f"  review  {summary.get('review_label', '0B')}  ({summary.get('review_n', 0)} items)",
        f"  keep    {summary.get('keep_label', '0B')}  ({summary.get('keep_n', 0)} items)",
        "",
    ]
    for title, key in (("DELETE — safe", "delete"), ("REVIEW — you decide", "review"), ("KEEP — leave it", "keep")):
        rows = advice.get(key) or []
        lines.append(title)
        if not rows:
            lines.append("  (none)")
        for row in rows[:25]:
            lines.append(f"  {row['label']:>8}  {row['name']}  — {row['reason']}")
        lines.append("")
    return "\n".join(lines)


def is_safe_delete(path: Path, advice: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Whether the app may trash *path* from the Delete column without a force flag."""
    path = path.expanduser()
    try:
        path = path.resolve()
    except OSError as exc:
        return False, str(exc)
    if is_protected(path) or dangerous_target(path):
        return False, "protected path"
    if advice:
        for row in advice.get("delete") or []:
            try:
                if Path(row["path"]).resolve() == path:
                    return True, "advised delete"
            except OSError:
                if row.get("path") == str(path):
                    return True, "advised delete"
        return False, "not on the delete list"
    bucket, _reason, kind = classify_path(path, is_dir=path.is_dir())
    if bucket == "delete" and kind in {"cache", "junk", "empty", "installer"}:
        return True, kind
    return False, "not a safe delete"
