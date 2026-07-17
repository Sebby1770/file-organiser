"""Content-based duplicate reporting using file size and SHA-256."""

from __future__ import annotations

import os
import stat
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .errors import SafetyError
from .models import DuplicateGroup, DuplicateReport
from .rules import DEFAULT_IGNORE_PATTERNS
from .scanner import scan_files
from .utils import fingerprint_file, resolve_root


def find_duplicates(
    root: str | os.PathLike[str],
    *,
    recursive: bool = False,
    ignore_patterns: Iterable[str] = (),
    include_hidden: bool = False,
) -> DuplicateReport:
    """Find duplicate groups, hashing only files that share a byte size."""

    target = resolve_root(root)
    patterns = tuple(dict.fromkeys((*DEFAULT_IGNORE_PATTERNS, *ignore_patterns)))
    files, skipped = scan_files(
        target,
        recursive=recursive,
        ignore_patterns=patterns,
        include_hidden=include_hidden,
    )
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in files:
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise SafetyError(f"Could not stat {path}: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise SafetyError(
                f"File changed to a non-regular entry while scanning: {path}"
            )
        by_size[info.st_size].append(path)

    groups: list[DuplicateGroup] = []
    for size, candidates in sorted(by_size.items()):
        if len(candidates) < 2:
            continue
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for path in candidates:
            fingerprint = fingerprint_file(path, target)
            if fingerprint.size != size:
                raise SafetyError(f"File changed size while scanning: {path}")
            by_hash[fingerprint.sha256].append(path)
        for sha256, matching in sorted(by_hash.items()):
            if len(matching) > 1:
                groups.append(
                    DuplicateGroup(size=size, sha256=sha256, files=tuple(matching))
                )

    groups.sort(
        key=lambda group: (
            group.size,
            group.sha256,
            tuple(path.relative_to(target).as_posix() for path in group.files),
        )
    )
    return DuplicateReport(
        root=target,
        recursive=recursive,
        scanned_files=len(files),
        groups=tuple(groups),
        skipped=skipped,
    )
