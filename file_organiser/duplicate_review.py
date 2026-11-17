"""Compare distinct copies and act on a reviewed desktop selection."""

from __future__ import annotations

import secrets
import stat
from pathlib import Path
from typing import Any, Callable, NamedTuple

from .duplicates import check_copy, find_duplicates, verify_copies
from .scanner import format_size

TrashPolicy = Callable[[Path], tuple[bool, str]]


class ReviewedGroup(NamedTuple):
    digest: str
    members: tuple[Path, ...]
    eligible: frozenset[Path]


class DuplicateReview:
    def __init__(self) -> None:
        self.groups: dict[str, ReviewedGroup] = {}

    def search(self, root: Path, min_size: int, policy: TrashPolicy) -> dict[str, Any]:
        # Desktop searches must work on read-only volumes and leave no cache.
        self.groups.clear()
        found = find_duplicates(root, min_size=min_size, use_cache=False)
        rows = []
        total_bytes = extras = skipped = 0
        for digest, paths in found.items():
            files = []
            identities = set()
            for path in sorted(paths):
                try:
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path:
                        skipped += 1
                        continue
                    eligible, reason = policy(path)
                except (OSError, RuntimeError):
                    skipped += 1
                    continue
                identities.add((info.st_dev, info.st_ino) if info.st_ino else path)
                if info.st_nlink > 1:
                    eligible, reason = False, "Hard-linked file"
                files.append({
                    "path": str(path), "relative": path.relative_to(root).as_posix(),
                    "name": path.name, "size": info.st_size, "label": format_size(info.st_size),
                    "mtime": info.st_mtime, "eligible": eligible, "reason": reason,
                })

            if len(identities) < 2:
                continue
            # Prefer a copy that the cleanup policy already preserves, then
            # the oldest file. Path ordering resolves timestamp ties.
            keeper = min(files, key=lambda f: (f["eligible"], f["mtime"], f["relative"]))
            group_id = secrets.token_urlsafe(18)
            self.groups[group_id] = ReviewedGroup(
                digest, tuple(Path(f["path"]) for f in files),
                frozenset(Path(f["path"]) for f in files if f["eligible"]),
            )
            extra_count = len(identities) - 1
            size = keeper["size"]
            rows.append({
                "group": group_id, "count": len(files), "copies": len(identities),
                "size": size, "extra_bytes": size * extra_count, "keep": keeper["path"],
                "files": files,
            })
            extras += extra_count
            total_bytes += size * extra_count
        rows.sort(key=lambda group: (-group["extra_bytes"], group["keep"]))
        return {
            "ok": True, "groups": rows, "extra_files": extras, "reclaim": total_bytes,
            "reclaim_label": format_size(total_bytes), "skipped": skipped,
        }

    def purge(self, body: dict[str, Any], policy: TrashPolicy, trash: Callable[[Path], str]) -> dict[str, Any]:
        group_id = str(body.get("group") or "")
        known = self.groups.get(group_id)
        if not known:
            return {"ok": False, "error": "Search again to review the current copies."}
        digest, members, eligible = known
        keeper = Path(str(body.get("keep") or ""))
        if keeper not in members:
            return {"ok": False, "error": "Choose a keeper from this group."}
        requested = body.get("paths")
        if not isinstance(requested, list) or not requested or not all(isinstance(p, str) for p in requested):
            return {"ok": False, "error": "Select at least one copy to move to Trash."}
        selected = [Path(value) for value in requested]
        if len(set(selected)) != len(selected) or keeper in selected or not set(selected).issubset(eligible):
            return {"ok": False, "error": "Select only eligible extras and leave the keeper unchecked."}

        def check_eligible(path: Path) -> None:
            safe, reason = policy(path)
            if not safe:
                raise ValueError(reason)
            if path.lstat().st_nlink > 1:
                raise ValueError("Hard-linked copies can only be reviewed.")

        try:
            for path in selected:
                check_eligible(path)
            verified = verify_copies(members, digest)
        except (ValueError, OSError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}

        # Once any move is attempted, retrying requires a new review.
        del self.groups[group_id]
        done = []
        skipped = []
        for index, path in enumerate(selected):
            try:
                check_copy(keeper, verified)
                check_copy(path, verified)
                check_eligible(path)
                done.append({"path": str(path), "action": trash(path)})
            except (ValueError, OSError, RuntimeError) as exc:
                skipped.append({"path": str(path), "error": str(exc)})
                skipped.extend({"path": str(p), "error": "Stopped after the previous error."} for p in selected[index + 1:])
                break
        return {"ok": True, "n": len(done), "files": done, "skipped": skipped}
