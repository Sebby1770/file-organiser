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
from typing import Any, Dict, List

from .safety import SafetyError, validate_category_name

# Default categories. Extensions must be lowercase and include the dot.
DEFAULT_RULES: Dict[str, List[str]] = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tiff", ".ico", ".heic", ".heif", ".raw", ".cr2", ".nef"],
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".tex", ".pages"],
    "Ebooks": [".epub", ".mobi", ".azw", ".azw3", ".fb2"],
    "Spreadsheets": [".xls", ".xlsx", ".csv", ".ods", ".tsv"],
    "Presentations": [".ppt", ".pptx", ".odp", ".key"],
    "Videos": [".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".m4v"],
    "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"],
    "Archives": [".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz", ".iso", ".img"],
    "Design": [".psd", ".ai", ".fig", ".sketch", ".xd", ".indd"],
    "Models": [".obj", ".fbx", ".stl", ".glb", ".gltf", ".blend"],
    "Code": [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
        ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt",
        ".sh", ".bash", ".html", ".css", ".scss", ".json", ".xml", ".yml", ".yaml",
    ],
    "Executables": [".exe", ".msi", ".dmg", ".deb", ".rpm", ".appimage"],
    "Fonts": [".ttf", ".otf", ".woff", ".woff2"],
}

OTHER_CATEGORY = "Other"
DEFAULT_PROFILE = "standard"


def _combined(*categories: str) -> List[str]:
    values: List[str] = []
    for category in categories:
        values.extend(DEFAULT_RULES[category])
    return values


# Profiles intentionally describe different folder layouts, not just filters.
# Every built-in profile covers every default extension so selecting a profile
# never silently demotes known files to Other.
BUILTIN_PROFILES: Dict[str, Dict[str, Any]] = {
    "standard": {
        "description": "Detailed folders for each common file type.",
        "rules": DEFAULT_RULES,
    },
    "downloads": {
        "description": "A compact Downloads layout with fewer top-level folders.",
        "rules": {
            "Pictures": _combined("Images", "Design"),
            "Documents": _combined(
                "Documents", "Ebooks", "Spreadsheets", "Presentations"
            ),
            "Media": _combined("Videos", "Audio"),
            "Archives": list(DEFAULT_RULES["Archives"]),
            "Applications": list(DEFAULT_RULES["Executables"]),
            "Code": list(DEFAULT_RULES["Code"]),
            "3D Models": list(DEFAULT_RULES["Models"]),
            "Fonts": list(DEFAULT_RULES["Fonts"]),
        },
    },
    "minimal": {
        "description": "Five broad folders for a low-maintenance layout.",
        "rules": {
            "Media": _combined("Images", "Design", "Videos", "Audio", "Models", "Fonts"),
            "Documents": _combined(
                "Documents", "Ebooks", "Spreadsheets", "Presentations"
            ),
            "Archives": list(DEFAULT_RULES["Archives"]),
            "Applications": list(DEFAULT_RULES["Executables"]),
            "Code": list(DEFAULT_RULES["Code"]),
        },
    },
}

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


def _normalize_rules(data: object, *, source: str) -> Dict[str, List[str]]:
    if not isinstance(data, dict) or not data:
        raise ValueError(
            f"{source} must map category names to non-empty extension lists."
        )

    normalized: Dict[str, List[str]] = {}
    owners: Dict[str, str] = {}
    category_names: Dict[str, str] = {}
    for raw_category, exts in data.items():
        try:
            category = validate_category_name(raw_category)
        except SafetyError as exc:
            raise ValueError(f"Invalid category in {source}: {exc}") from exc
        folded_category = category.casefold()
        previous_category = category_names.get(folded_category)
        if previous_category is not None and previous_category != category:
            raise ValueError(
                f"Category names '{previous_category}' and '{category}' collide "
                "on a case-insensitive filesystem."
            )
        category_names[folded_category] = category
        if not isinstance(exts, list) or not exts:
            raise ValueError(
                f"Category '{category}' in {source} must map to a non-empty list "
                "of extensions."
            )
        values: List[str] = []
        for raw_extension in exts:
            if not isinstance(raw_extension, str) or not raw_extension.strip():
                raise ValueError(
                    f"Category '{category}' in {source} has a non-string or empty extension."
                )
            extension = raw_extension.strip().lower()
            if any(separator in extension for separator in ("/", "\\", "\x00")):
                raise ValueError(
                    f"Unsafe extension {raw_extension!r} in category '{category}'."
                )
            if not extension.startswith("."):
                extension = f".{extension}"
            previous = owners.get(extension)
            if previous is not None and previous != category:
                raise ValueError(
                    f"Extension {extension!r} is assigned to both "
                    f"'{previous}' and '{category}'."
                )
            owners[extension] = category
            if extension not in values:
                values.append(extension)
        normalized[category] = values
    return normalized


def _read_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a JSON object.")
    return data


def load_rules(
    config_path: Path | None = None,
    profile: str | None = None,
) -> Dict[str, List[str]]:
    """Load a built-in, legacy custom, or named custom profile.

    Legacy JSON still maps categories directly to extension lists::

        {"Images": [".jpg", ".png"], "Docs": [".pdf"]}

    A profile config uses ``{"version": 1, "default_profile": "work",
    "profiles": {"work": {"rules": {...}}}}``.
    """
    if config_path is None:
        selected = profile or DEFAULT_PROFILE
        spec = BUILTIN_PROFILES.get(selected)
        if spec is None:
            choices = ", ".join(sorted(BUILTIN_PROFILES))
            raise ValueError(f"Unknown built-in profile '{selected}'. Choose: {choices}")
        return _normalize_rules(spec["rules"], source=f"profile '{selected}'")

    data = _read_config(config_path)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        if profile and profile not in {"custom", "default"}:
            raise ValueError(
                "This legacy config has one unnamed profile; omit --profile."
            )
        return _normalize_rules(data, source=str(config_path))

    if data.get("version", 1) != 1:
        raise ValueError(f"Unsupported profile config version: {data.get('version')}")
    selected = profile or data.get("default_profile")
    if selected is None:
        selected = next(iter(profiles), None)
    if not isinstance(selected, str) or selected not in profiles:
        choices = ", ".join(sorted(str(name) for name in profiles))
        raise ValueError(f"Unknown profile '{selected}'. Choose: {choices}")
    spec = profiles[selected]
    rules_data = spec.get("rules") if isinstance(spec, dict) and "rules" in spec else spec
    return _normalize_rules(
        rules_data,
        source=f"profile '{selected}' in {config_path}",
    )


def available_profiles(config_path: Path | None = None) -> List[Dict[str, str]]:
    """Return profile names and descriptions for CLI/UI discovery."""
    if config_path is None:
        return [
            {
                "name": name,
                "description": str(spec.get("description", "")),
                "source": "built-in",
            }
            for name, spec in sorted(BUILTIN_PROFILES.items())
        ]
    data = _read_config(config_path)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return [
            {
                "name": "custom",
                "description": "Legacy single-profile rules file.",
                "source": str(config_path),
            }
        ]
    result: List[Dict[str, str]] = []
    for raw_name, spec in sorted(profiles.items()):
        name = validate_category_name(raw_name)
        description = spec.get("description", "") if isinstance(spec, dict) else ""
        result.append(
            {
                "name": name,
                "description": str(description),
                "source": str(config_path),
            }
        )
    return result


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
    use_magic: bool = False,
    use_smart: bool = False,
) -> str:
    """Categorize a file by extension, then optional smart name / magic / MIME.

    Extension always wins when it matches a rule. Smart names, magic bytes, and
    MIME are consulted only for Other — they do not override a known type.
    """
    cat = category_for_extension(path.suffix, rules)
    if cat != OTHER_CATEGORY:
        return cat
    if use_smart:
        from .smart import smart_category

        hinted = smart_category(path)
        if hinted and hinted != OTHER_CATEGORY:
            return hinted
    if use_magic:
        from .magic import sniff_category

        sniffed = sniff_category(path)
        if sniffed != OTHER_CATEGORY:
            return sniffed
    if use_mime:
        mime, _ = mimetypes.guess_type(str(path))
        return category_for_mime(mime)
    return OTHER_CATEGORY


def all_category_names(rules: Dict[str, List[str]] | None = None) -> List[str]:
    """Return sorted category names including Other."""
    rules = rules or DEFAULT_RULES
    names = list(rules.keys())
    if OTHER_CATEGORY not in names:
        names.append(OTHER_CATEGORY)
    return sorted(names)


def xdg_config_path() -> Path:
    """Return the default user config path for rules.json."""
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / XDG_CONFIG_REL
    return Path.home() / ".config" / XDG_CONFIG_REL


def local_config_path(cwd: Path | None = None) -> Path:
    """Return path for a project-local ``.file-organiser.json``."""
    base = cwd if cwd is not None else Path.cwd()
    return base / LOCAL_CONFIG_NAME


def init_config(
    *,
    local: bool = False,
    force: bool = False,
    cwd: Path | None = None,
) -> Path:
    """Write default rules JSON to XDG path or local ``.file-organiser.json``.

    Returns the path written. Raises ``FileExistsError`` if the file exists
    and *force* is False.
    """
    if local:
        path = local_config_path(cwd)
    else:
        path = xdg_config_path()

    path = path.expanduser()
    if path.exists() and not force:
        raise FileExistsError(
            f"Config already exists: {path} (use --force to overwrite)"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    # Write pretty JSON of DEFAULT_RULES
    payload = {k: list(v) for k, v in DEFAULT_RULES.items()}
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    return path
