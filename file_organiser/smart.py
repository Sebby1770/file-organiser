"""Name-based hints for files whose extension is missing or useless."""

from __future__ import annotations

import re
from pathlib import Path

from .rules import OTHER_CATEGORY

_SCREEN = re.compile(r"(screenshot|screen[ _-]?shot|screencap|screenclip)", re.I)
_CAMERA = re.compile(r"^(img|dsc|dcim|photo|pxl|mvimg)[_-]?\d", re.I)
_INVOICE = re.compile(r"(invoice|receipt|statement|tax[-_ ]?return|payslip)", re.I)
_RESUME = re.compile(r"(resume|cv|curriculum)", re.I)


def smart_category(path: Path) -> str:
    """Guess a category from the filename. Empty string means 'no opinion'."""
    name = path.name
    stem = path.stem
    if _SCREEN.search(name):
        return "Images"
    if _CAMERA.search(stem):
        return "Images"
    if _INVOICE.search(name):
        return "Documents"
    if _RESUME.search(name):
        return "Documents"
    if stem.lower() in {"untitled", "download", "new document", "new folder"}:
        return OTHER_CATEGORY
    return ""
