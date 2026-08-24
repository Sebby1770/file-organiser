"""Sniff a few file-header bytes when the extension is lying or missing."""

from __future__ import annotations

from pathlib import Path

from .rules import OTHER_CATEGORY

# (prefix, offset, category) — prefix is raw bytes at offset.
_SIGNATURES: tuple[tuple[bytes, int, str], ...] = (
    (b"\xff\xd8\xff", 0, "Images"),
    (b"\x89PNG\r\n\x1a\n", 0, "Images"),
    (b"GIF87a", 0, "Images"),
    (b"GIF89a", 0, "Images"),
    (b"BM", 0, "Images"),
    (b"RIFF", 0, "Images"),  # WEBP/AVI — refined below
    (b"%PDF", 0, "Documents"),
    (b"PK\x03\x04", 0, "Archives"),
    (b"Rar!\x1a\x07", 0, "Archives"),
    (b"\x1f\x8b", 0, "Archives"),
    (b"7z\xbc\xaf'\x1c", 0, "Archives"),
    (b"ID3", 0, "Audio"),
    (b"\xff\xfb", 0, "Audio"),
    (b"fLaC", 0, "Audio"),
    (b"OggS", 0, "Audio"),
    (b"\x00\x00\x00\x18ftyp", 4, "Videos"),  # handled specially
    (b"\x7fELF", 0, "Executables"),
    (b"MZ", 0, "Executables"),
)


def sniff_category(path: Path, nbytes: int = 32) -> str:
    """Return a category from magic bytes, or Other if unknown/unreadable."""
    try:
        with path.open("rb") as handle:
            head = handle.read(nbytes)
    except OSError:
        return OTHER_CATEGORY
    if len(head) < 2:
        return OTHER_CATEGORY
    if head.startswith(b"RIFF") and len(head) >= 12:
        kind = head[8:12]
        if kind == b"WEBP":
            return "Images"
        if kind in {b"AVI ", b"AVIX"}:
            return "Videos"
        if kind == b"WAVE":
            return "Audio"
    if b"ftyp" in head[:16]:
        return "Videos"
    if head.startswith(b"PK\x03\x04"):
        # Office Open XML is a zip. Trust a known office extension.
        suffix = path.suffix.lower()
        if suffix in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub"}:
            mapping = {
                ".docx": "Documents",
                ".odt": "Documents",
                ".epub": "Ebooks",
                ".xlsx": "Spreadsheets",
                ".ods": "Spreadsheets",
                ".pptx": "Presentations",
                ".odp": "Presentations",
            }
            return mapping.get(suffix, "Archives")
        return "Archives"
    for prefix, offset, category in _SIGNATURES:
        if offset == 0 and head.startswith(prefix):
            return category
        if offset and len(head) >= offset + len(prefix) and head[offset : offset + len(prefix)] == prefix:
            return category
    return OTHER_CATEGORY
