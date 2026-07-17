"""Deterministic, symlink-averse filesystem scanning."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable

from .errors import SafetyError
from .models import SkippedItem
from .utils import ensure_lexically_within


def _sort_key(name: str) -> tuple[str, str]:
    return name.casefold(), name


def _matches_ignore(relative: Path, patterns: tuple[str, ...]) -> bool:
    path_text = relative.as_posix()
    return any(
        fnmatch.fnmatchcase(path_text, pattern)
        or fnmatch.fnmatchcase(relative.name, pattern)
        for pattern in patterns
    )


def _is_hidden(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)


def scan_files(
    root: Path,
    *,
    recursive: bool,
    ignore_patterns: Iterable[str] = (),
    include_hidden: bool = False,
    managed_directories: Iterable[str] = (),
    protected_paths: Iterable[Path] = (),
) -> tuple[tuple[Path, ...], tuple[SkippedItem, ...]]:
    """Return regular files in deterministic order without following symlinks."""

    patterns = tuple(dict.fromkeys(ignore_patterns))
    managed = {name.casefold() for name in managed_directories}
    protected = {Path(path) for path in protected_paths}
    files: list[Path] = []
    skipped: list[SkippedItem] = []
    pending = [root]

    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: _sort_key(entry.name))
        except OSError as exc:
            raise SafetyError(f"Could not scan directory {directory}: {exc}") from exc

        child_directories: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            ensure_lexically_within(path, root)
            relative = path.relative_to(root)

            if path in protected:
                skipped.append(SkippedItem(path, "protected-file"))
                continue
            ignored = _matches_ignore(relative, patterns) or (
                not include_hidden and _is_hidden(relative)
            )
            if ignored:
                try:
                    is_directory = entry.is_dir(follow_symlinks=False)
                except OSError:
                    is_directory = False
                skipped.append(
                    SkippedItem(
                        path,
                        "ignored-directory" if is_directory else "ignored",
                    )
                )
                continue
            try:
                if entry.is_symlink():
                    skipped.append(SkippedItem(path, "symlink"))
                elif entry.is_dir(follow_symlinks=False):
                    if directory == root and entry.name.casefold() in managed:
                        skipped.append(SkippedItem(path, "managed-directory"))
                    elif recursive:
                        child_directories.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
                else:
                    skipped.append(SkippedItem(path, "non-regular"))
            except OSError as exc:
                raise SafetyError(
                    f"Could not inspect filesystem entry {path}: {exc}"
                ) from exc

        # Stack reversal preserves lexical order during depth-first traversal.
        pending.extend(reversed(child_directories))

        if not recursive:
            break

    return tuple(files), tuple(skipped)
