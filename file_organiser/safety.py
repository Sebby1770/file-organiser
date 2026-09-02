"""Safety checks shared by planning, applying, and undoing operations.

The organiser deliberately treats saved plans and custom rule files as
untrusted input. Every mutating path is checked lexically *and* after
resolving existing path components, and existing symlink components are
rejected. This prevents a category such as ``../Elsewhere`` or a symlinked
``Documents`` directory from redirecting a move outside the selected root.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterator


class SafetyError(ValueError):
    """Raised when an operation would cross a safety boundary."""


def dangerous_target(folder: Path) -> str | None:
    """Return a reason if *folder* is too broad, else ``None``."""
    try:
        resolved = folder.expanduser().resolve()
    except OSError:
        return None
    home = Path.home().resolve()
    roots = {Path("/").resolve()}
    if resolved in roots:
        return "refusing to organize the filesystem root"
    if resolved == home:
        return "refusing to organize the home directory (too broad)"
    # Drive roots on Windows, e.g. C:\
    if len(resolved.parts) == 1:
        return "refusing to organize a drive root"
    return None


def validate_category_name(category: object) -> str:
    """Validate and return a category as one safe directory name.

    Categories are labels, not paths. Both POSIX and Windows separators are
    rejected on every platform so a config cannot become dangerous when it is
    copied to another operating system.
    """
    if not isinstance(category, str):
        raise SafetyError("category names must be strings")
    name = category.strip()
    if not name:
        raise SafetyError("category names cannot be empty")
    if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise SafetyError(f"unsafe category name: {category!r}")
    if Path(name).is_absolute():
        raise SafetyError(f"category name must not be absolute: {category!r}")
    if name.endswith((" ", ".")):
        raise SafetyError(
            f"category name cannot end with a space or dot: {category!r}"
        )
    if any(ord(char) < 32 or char in '<>:"|?*' for char in name):
        raise SafetyError(
            f"category name contains characters invalid on Windows: {category!r}"
        )
    device = name.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{number}" for number in range(1, 10))
    reserved.update(f"LPT{number}" for number in range(1, 10))
    if device in reserved:
        raise SafetyError(f"category name is reserved on Windows: {category!r}")
    return name


def resolved_root(folder: Path) -> Path:
    """Return a canonical existing directory root or raise ``SafetyError``."""
    try:
        root = folder.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafetyError(f"cannot resolve folder {folder}: {exc}") from exc
    if not root.is_dir():
        raise SafetyError(f"not a directory: {folder}")
    return root


def _absolute_without_resolving(path: Path) -> Path:
    """Normalize ``.``/``..`` without resolving symlinks."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def is_within(path: Path, root: Path, *, resolve: bool = True) -> bool:
    """Return whether *path* is contained by *root*.

    ``resolve=True`` follows existing path components and is therefore useful
    for spotting a symlink escape. Callers generally check both lexical and
    resolved containment.
    """
    try:
        root_value = root.resolve(strict=False)
        path_value = (
            path.resolve(strict=False)
            if resolve
            else _absolute_without_resolving(path)
        )
        path_value.relative_to(root_value)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _components_below(
    root: Path,
    path: Path,
    *,
    include_leaf: bool,
) -> Iterator[Path]:
    root_abs = _absolute_without_resolving(root)
    path_abs = _absolute_without_resolving(path)
    try:
        rel = path_abs.relative_to(root_abs)
    except ValueError as exc:
        raise SafetyError(f"path is outside selected folder: {path}") from exc
    parts = rel.parts if include_leaf else rel.parts[:-1]
    current = root_abs
    for part in parts:
        current = current / part
        yield current


def ensure_no_symlink_components(
    root: Path,
    path: Path,
    *,
    include_leaf: bool = False,
) -> None:
    """Reject existing symlinks between *root* and *path*."""
    for component in _components_below(root, path, include_leaf=include_leaf):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SafetyError(
                f"cannot inspect path component {component}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise SafetyError(f"symlink path component is not allowed: {component}")


def validate_source_path(root: Path, source: Path) -> Path:
    """Return a canonical, regular, non-symlink source contained by *root*."""
    root = resolved_root(root)
    source_abs = _absolute_without_resolving(source)
    if not is_within(source_abs, root, resolve=False):
        raise SafetyError(f"source is outside selected folder: {source}")
    ensure_no_symlink_components(root, source_abs, include_leaf=True)
    try:
        mode = source_abs.lstat().st_mode
    except OSError as exc:
        raise SafetyError(f"source is missing or unreadable: {source}") from exc
    if not stat.S_ISREG(mode):
        raise SafetyError(f"source is not a regular file: {source}")
    try:
        resolved = source_abs.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafetyError(f"cannot resolve source {source}: {exc}") from exc
    if not is_within(resolved, root):
        raise SafetyError(f"source resolves outside selected folder: {source}")
    return resolved


def validate_destination_path(
    root: Path,
    destination: Path,
    *,
    allow_existing: bool = True,
) -> Path:
    """Return a safe absolute destination contained by *root*.

    The leaf may exist when ``allow_existing`` is true, but it may never be a
    directory or symlink. Parent symlinks are always refused.
    """
    root = resolved_root(root)
    destination_abs = _absolute_without_resolving(destination)
    if destination_abs == root or not is_within(
        destination_abs, root, resolve=False
    ):
        raise SafetyError(f"destination is outside selected folder: {destination}")
    ensure_no_symlink_components(root, destination_abs, include_leaf=False)
    if not is_within(destination_abs.parent, root, resolve=True):
        raise SafetyError(f"destination resolves outside selected folder: {destination}")
    try:
        mode = destination_abs.lstat().st_mode
    except FileNotFoundError:
        mode = None
    except OSError as exc:
        raise SafetyError(f"cannot inspect destination {destination}: {exc}") from exc
    if mode is not None:
        if stat.S_ISLNK(mode):
            raise SafetyError(f"symlink destination is not allowed: {destination}")
        if not stat.S_ISREG(mode):
            raise SafetyError(f"destination is not a regular file: {destination}")
        if not allow_existing:
            raise SafetyError(f"destination already exists: {destination}")
    return destination_abs


def validate_internal_path(root: Path, path: Path) -> Path:
    """Validate a transaction-owned path below ``.file-organiser``."""
    root = resolved_root(root)
    internal = root / ".file-organiser"
    candidate = _absolute_without_resolving(path)
    if not is_within(candidate, internal, resolve=False):
        raise SafetyError(f"invalid internal transaction path: {path}")
    # Internal directories and files are never allowed to be symlinks. Check
    # the leaf as well as its parents so a pre-created transaction path cannot
    # redirect backup or undo writes outside the selected folder.
    ensure_no_symlink_components(root, candidate, include_leaf=True)
    return candidate
