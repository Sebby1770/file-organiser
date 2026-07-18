"""Filesystem, hashing, and serialization safety helpers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import SafetyError
from .models import FileFingerprint

HASH_CHUNK_SIZE = 1024 * 1024


def utc_now() -> str:
    """Return an RFC 3339 UTC timestamp with microsecond precision."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def resolve_root(path: str | os.PathLike[str]) -> Path:
    """Resolve and validate an organization root without following a root symlink."""

    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise SafetyError(f"Refusing symlink as the target root: {candidate}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SafetyError(f"Target directory does not exist: {candidate}") from exc
    if not resolved.is_dir():
        raise SafetyError(f"Target is not a directory: {resolved}")
    if resolved == Path(resolved.anchor):
        raise SafetyError(f"Refusing to operate on a filesystem root: {resolved}")
    return resolved


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def ensure_lexically_within(path: Path, root: Path) -> None:
    """Reject paths that are not lexically rooted below ``root``."""

    absolute = Path(os.path.abspath(path))
    if absolute == root or not is_relative_to(absolute, root):
        raise SafetyError(f"Path escapes target root: {path}")


def ensure_no_symlink_components(path: Path, root: Path, *, leaf: bool = True) -> None:
    """Reject an existing symlink anywhere between root and path."""

    ensure_lexically_within(path, root)
    relative_parts = path.relative_to(root).parts
    parts = relative_parts if leaf else relative_parts[:-1]
    current = root
    for part in parts:
        current = current / part
        if os.path.lexists(current) and current.is_symlink():
            raise SafetyError(f"Refusing path with symlink component: {current}")


def safe_join(root: Path, relative: object) -> Path:
    """Join a manifest's POSIX relative path to root after strict validation."""

    if not isinstance(relative, str) or not relative:
        raise SafetyError("Manifest path must be a non-empty string")
    if "\\" in relative or ":" in relative or "\x00" in relative:
        raise SafetyError(f"Unsafe platform path syntax in manifest: {relative!r}")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise SafetyError(f"Unsafe path in manifest: {relative!r}")
    path = root.joinpath(*pure.parts)
    ensure_lexically_within(path, root)
    return path


def fingerprint_file(path: Path, root: Path | None = None) -> FileFingerprint:
    """Hash a stable regular file without following symlinks.

    The file descriptor is checked against the lstat result, then checked again
    after hashing. A concurrently replaced or modified file is rejected.
    """

    if root is not None:
        ensure_no_symlink_components(path, root)
    try:
        before = os.lstat(path)
    except FileNotFoundError as exc:
        raise SafetyError(f"File disappeared while planning: {path}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise SafetyError(f"Refusing to hash symlink: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise SafetyError(f"Refusing non-regular file: {path}")

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise SafetyError(f"File changed while opening it: {path}")
            while chunk := stream.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise SafetyError(f"Could not read file safely: {path}: {exc}") from exc

    if (
        (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise SafetyError(f"File changed while it was being hashed: {path}")
    return FileFingerprint(
        size=after.st_size,
        sha256=digest.hexdigest(),
        mtime_ns=after.st_mtime_ns,
    )


def fingerprints_match(expected: FileFingerprint, actual: FileFingerprint) -> bool:
    """Compare content identity; mtimes are informational and may legitimately vary."""

    return expected.size == actual.size and expected.sha256 == actual.sha256


def atomic_write_json(path: Path, payload: object, *, private: bool = False) -> None:
    """Atomically write formatted JSON and fsync it before replacement."""

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        fchmod = getattr(os, "fchmod", None)
        if private and fchmod is not None:
            fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor_open = False
        with stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value
