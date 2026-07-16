"""Default rules mapping file categories to extensions.

Users can override these by providing a custom JSON config file
via the --config flag. See README.md for the expected format.

When extension is unknown/missing, optional MIME fallback uses
``mimetypes.guess_type`` to map content types to categories.
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional

# Default categories. Extensions must be lowercase and include the dot.
DEFAULT_RULES: Dict[str, List[str]] = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tiff", ".ico"],
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".tex"],
    "Spreadsheets": [".xls", ".xlsx", ".csv", ".ods", ".tsv"],
    "Presentations": [".ppt", ".pptx", ".odp", ".key"],
    "Videos": [".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".m4v"],
    "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"],
    "Archives": [".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz"],
    "Code": [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
        ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt",
        ".sh", ".bash", ".html", ".css", ".scss", ".json", ".xml", ".yml", ".yaml",
    ],
    "Executables": [".exe", ".msi", ".dmg", ".deb", ".rpm", ".appimage"],
    "Fonts": [".ttf", ".otf", ".woff", ".woff2"],
}

OTHER_CATEGORY = "Other"

# Local and XDG-style config discovery paths (checked when --config omitted).
LOCAL_CONFIG_NAME = ".file-organiser.json"
XDG_CONFIG_REL = Path("file-organiser") / "rules.json"

# MIME type / prefix → category (used when extension is unknown).
MIME_EXACT: Dict[str, str] = {
    "application/pdf": "Documents",
    "application/msword": "Documents",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Documents",
    "application/rtf": "Documents",
    "application/vnd.oasis.opendocument.text": "Documents",
    "application/vnd.ms-excel": "Spreadsheets",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Spreadsheets",
    "application/vnd.oasis.opendocument.spreadsheet": "Spreadsheets",
    "application/vnd.ms-powerpoint": "Presentations",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Presentations",
    "application/zip": "Archives",
    "application/x-tar": "Archives",
    "application/gzip": "Archives",
    "application/x-rar-compressed": "Archives",
    "application/x-7z-compressed": "Archives",
    "application/x-bzip2": "Archives",
    "application/x-xz": "Archives",
    "application/javascript": "Code",
    "application/json": "Code",
    "application/xml": "Code",
    "application/x-sh": "Code",
    "application/x-python": "Code",
    "application/font-woff": "Fonts",
    "application/font-woff2": "Fonts",
    "font/ttf": "Fonts",
    "font/otf": "Fonts",
    "font/woff": "Fonts",
    "font/woff2": "Fonts",
}

MIME_PREFIXES: Dict[str, str] = {
    "image/": "Images",
    "video/": "Videos",
    "audio/": "Audio",
    "text/": "Documents",
}


def discover_config(explicit: Path | None = None) -> Path | None:
    """Resolve a rules config path.

    Order:
      1. Explicit ``--config`` path (if given)
      2. ``./.file-organiser.json``
      3. ``~/.config/file-organiser/rules.json`` (XDG)
    """
    if explicit is not None:
        return explicit.expanduser().resolve()

    local = Path.cwd() / LOCAL_CONFIG_NAME
    if local.is_file():
        return local.resolve()

    xdg_home = Path.home() / ".config" / XDG_CONFIG_REL
    if xdg_home.is_file():
        return xdg_home.resolve()

    # Honour XDG_CONFIG_HOME when set
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidate = Path(xdg) / XDG_CONFIG_REL
        if candidate.is_file():
            return candidate.resolve()

    return None


def load_rules(config_path: Path | None = None) -> Dict[str, List[str]]:
    """Load rules from a JSON file, or return the defaults.

    The JSON file should map category names to a list of extensions:
        {"Images": [".jpg", ".png"], "Docs": [".pdf"]}
    """
    if config_path is None:
        return dict(DEFAULT_RULES)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(
            "Config file must contain a JSON object mapping categories to extension lists."
        )

    # Normalize: lowercase extensions, ensure leading dot.
    normalized: Dict[str, List[str]] = {}
    for category, exts in data.items():
        if not isinstance(exts, list):
            raise ValueError(f"Category '{category}' must map to a list of extensions.")
        normalized[category] = [
            (e if e.startswith(".") else f".{e}").lower() for e in exts
        ]
    return normalized


def category_for_extension(ext: str, rules: Dict[str, List[str]]) -> str:
    """Return the category name for a given extension, or OTHER_CATEGORY."""
    ext = ext.lower()
    for category, extensions in rules.items():
        if ext in extensions:
            return category
    return OTHER_CATEGORY


def category_for_mime(mime: str | None) -> str:
    """Map a MIME type string to a category, or OTHER_CATEGORY."""
    if not mime:
        return OTHER_CATEGORY
    mime = mime.lower().split(";")[0].strip()
    if mime in MIME_EXACT:
        return MIME_EXACT[mime]
    for prefix, category in MIME_PREFIXES.items():
        if mime.startswith(prefix):
            return category
    return OTHER_CATEGORY


def category_for_path(
    path: Path,
    rules: Dict[str, List[str]],
    *,
    use_mime: bool = False,
) -> str:
    """Categorize a file by extension, optionally falling back to MIME type.

    When *use_mime* is True and the extension is unknown/missing, uses
    ``mimetypes.guess_type`` to pick a category. When the extension already
    matches a rule, MIME is not consulted.
    """
    cat = category_for_extension(path.suffix, rules)
    if cat != OTHER_CATEGORY or not use_mime:
        return cat
    mime, _ = mimetypes.guess_type(str(path))
    return category_for_mime(mime)


def all_category_names(rules: Dict[str, List[str]] | None = None) -> List[str]:
    """Return sorted category names including Other."""
    rules = rules or DEFAULT_RULES
    names = list(rules.keys())
    if OTHER_CATEGORY not in names:
        names.append(OTHER_CATEGORY)
    return sorted(names)
