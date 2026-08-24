"""Refuse to organize a home directory or filesystem root without an explicit flag."""

from __future__ import annotations

from pathlib import Path


def dangerous_target(folder: Path) -> str | None:
    """Return a reason if *folder* is too broad, else None."""
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
