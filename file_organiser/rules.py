"""Default rules mapping file categories to extensions.

Users can override these by providing a custom JSON config file
via the --config flag. See README.md for the expected format.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

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


def all_category_names(rules: Dict[str, List[str]] | None = None) -> List[str]:
    """Return sorted category names including Other."""
    rules = rules or DEFAULT_RULES
    names = list(rules.keys())
    if OTHER_CATEGORY not in names:
        names.append(OTHER_CATEGORY)
    return sorted(names)
