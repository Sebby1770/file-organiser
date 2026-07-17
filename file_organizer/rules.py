"""Validated built-in and JSON-configurable classification rules."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Mapping, Sequence

from .errors import ConfigurationError

DEFAULT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Archives": (".7z", ".bz2", ".gz", ".rar", ".tar", ".tar.gz", ".xz", ".zip"),
    "Audio": (".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".wma"),
    "Code": (
        ".bash",
        ".c",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scss",
        ".sh",
        ".swift",
        ".ts",
        ".tsx",
        ".xml",
        ".yaml",
        ".yml",
    ),
    "Documents": (".doc", ".docx", ".md", ".odt", ".pdf", ".rtf", ".tex", ".txt"),
    "Executables": (".appimage", ".deb", ".dmg", ".exe", ".msi", ".rpm"),
    "Fonts": (".otf", ".ttf", ".woff", ".woff2"),
    "Images": (
        ".bmp",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".tiff",
        ".webp",
    ),
    "Presentations": (".key", ".odp", ".ppt", ".pptx"),
    "Spreadsheets": (".csv", ".ods", ".tsv", ".xls", ".xlsx"),
    "Videos": (".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"),
}

DEFAULT_IGNORE_PATTERNS = (
    ".file-organizer",
    ".file-organizer/**",
    ".file-organizer-quarantine-*",
    ".file-organizer-quarantine-*/**",
    ".git",
    ".git/**",
    "__pycache__",
    "__pycache__/**",
)

WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class RuleSet:
    categories: Mapping[str, tuple[str, ...]]
    default_category: str = "Other"
    ignore_patterns: tuple[str, ...] = DEFAULT_IGNORE_PATTERNS
    include_hidden: bool = False
    source: str = "built-in"

    @property
    def managed_categories(self) -> tuple[str, ...]:
        return tuple(
            sorted((*self.categories.keys(), self.default_category), key=str.casefold)
        )

    def category_for(self, filename: str) -> str:
        lower_name = filename.casefold()
        matches: list[tuple[int, str]] = []
        for category, extensions in self.categories.items():
            for extension in extensions:
                if lower_name.endswith(extension):
                    matches.append((len(extension), category))
        if not matches:
            return self.default_category
        matches.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
        return matches[0][1]


def _validate_category(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field} must be a non-empty string")
    category = unicodedata.normalize("NFC", value.strip())
    windows_path = PureWindowsPath(category)
    reserved_stem = category.split(".", 1)[0].casefold()
    if (
        category in {".", ".."}
        or "/" in category
        or "\\" in category
        or ":" in category
        or "\x00" in category
        or category.startswith(".")
        or category.rstrip(" .") != category
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or reserved_stem in WINDOWS_RESERVED_NAMES
    ):
        raise ConfigurationError(f"Unsafe category name for {field}: {category!r}")
    return category


def _normalize_extension(value: object, category: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Category {category!r} contains an invalid extension")
    extension = value.strip().casefold()
    if not extension.startswith("."):
        extension = f".{extension}"
    if extension == "." or "/" in extension or "\\" in extension or "\x00" in extension:
        raise ConfigurationError(
            f"Category {category!r} contains unsafe extension {value!r}"
        )
    return extension


def _normalize_categories(value: object) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(value, dict) or not value:
        raise ConfigurationError("categories must be a non-empty JSON object")
    normalized: dict[str, tuple[str, ...]] = {}
    owners: dict[str, str] = {}
    category_owners: dict[str, str] = {}
    for raw_category, raw_extensions in value.items():
        category = _validate_category(raw_category, "category")
        category_key = category.casefold()
        previous_category = category_owners.get(category_key)
        if previous_category is not None:
            raise ConfigurationError(
                f"Categories {previous_category!r} and {category!r} collide after normalization"
            )
        category_owners[category_key] = category
        if not isinstance(raw_extensions, (list, tuple)) or not raw_extensions:
            raise ConfigurationError(
                f"Category {category!r} must contain a non-empty extension list"
            )
        extensions = tuple(
            sorted(
                {_normalize_extension(item, category) for item in raw_extensions},
                key=lambda item: (-len(item), item),
            )
        )
        for extension in extensions:
            previous = owners.get(extension)
            if previous is not None:
                raise ConfigurationError(
                    f"Extension {extension!r} is assigned to both {previous!r} and {category!r}"
                )
            owners[extension] = category
        normalized[category] = extensions
    return MappingProxyType(
        dict(sorted(normalized.items(), key=lambda item: (item[0].casefold(), item[0])))
    )


def _normalize_patterns(value: object) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_IGNORE_PATTERNS
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or "\x00" in item for item in value
    ):
        raise ConfigurationError("ignore_patterns must be a list of non-empty strings")
    return tuple(dict.fromkeys((*DEFAULT_IGNORE_PATTERNS, *value)))


def default_rules() -> RuleSet:
    return RuleSet(categories=_normalize_categories(DEFAULT_CATEGORIES))


def load_rules(config_path: Path | None = None) -> RuleSet:
    """Load rich or legacy JSON rules and return an immutable validated rule set.

    Legacy shape::

        {"Images": [".jpg", ".png"], "Documents": ["pdf"]}

    Rich shape::

        {
          "categories": {"Images": [".jpg"]},
          "default_category": "Other",
          "ignore_patterns": ["*.part"],
          "include_hidden": false
        }
    """

    if config_path is None:
        return default_rules()
    path = Path(config_path).expanduser()
    try:
        path = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Rules file does not exist: {path}") from exc
    if path.is_dir():
        raise ConfigurationError(f"Rules path is a directory: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"Could not read rules file {path}: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ConfigurationError("Rules file must contain a non-empty JSON object")

    rich_keys = {"categories", "default_category", "ignore_patterns", "include_hidden"}
    if "categories" in data:
        unknown = set(data).difference(rich_keys)
        if unknown:
            raise ConfigurationError(
                f"Unknown rules setting(s): {', '.join(sorted(unknown))}"
            )
        categories_value = data["categories"]
        default_category = _validate_category(
            data.get("default_category", "Other"), "default_category"
        )
        ignore_patterns = _normalize_patterns(data.get("ignore_patterns"))
        include_hidden = data.get("include_hidden", False)
        if not isinstance(include_hidden, bool):
            raise ConfigurationError("include_hidden must be true or false")
    else:
        if set(data).intersection(rich_keys):
            raise ConfigurationError(
                "Rich rules files must include a categories object"
            )
        categories_value = data
        default_category = "Other"
        ignore_patterns = DEFAULT_IGNORE_PATTERNS
        include_hidden = False

    categories = _normalize_categories(categories_value)
    if default_category.casefold() in {category.casefold() for category in categories}:
        raise ConfigurationError(
            "default_category must not also be a configured category"
        )
    return RuleSet(
        categories=categories,
        default_category=default_category,
        ignore_patterns=ignore_patterns,
        include_hidden=include_hidden,
        source=str(path),
    )


def category_for_extension(
    extension: str, rules: RuleSet | Mapping[str, Sequence[str]]
) -> str:
    """Compatibility helper retained for callers of the original module."""

    if isinstance(rules, RuleSet):
        return rules.category_for(f"file{extension}")
    normalized = _normalize_categories(
        {category: list(extensions) for category, extensions in rules.items()}
    )
    return RuleSet(categories=normalized).category_for(f"file{extension}")
