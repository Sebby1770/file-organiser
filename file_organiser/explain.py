"""Explain why a path received a category."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from .magic import sniff_category
from .rules import OTHER_CATEGORY, category_for_extension, category_for_mime, category_for_path, load_rules
from .smart import smart_category


def explain_path(
    path: Path,
    rules: dict[str, list[str]] | None = None,
    *,
    use_mime: bool = True,
    use_magic: bool = True,
    use_smart: bool = True,
) -> dict[str, Any]:
    rules = rules or load_rules(None)
    ext = path.suffix
    ext_cat = category_for_extension(ext, rules)
    hinted = smart_category(path) if use_smart else ""
    sniffed = sniff_category(path) if use_magic and path.is_file() else OTHER_CATEGORY
    mime, _ = mimetypes.guess_type(str(path)) if use_mime else (None, None)
    mime_cat = category_for_mime(mime)
    final = category_for_path(
        path, rules, use_mime=use_mime, use_magic=use_magic, use_smart=use_smart
    )
    reasons = [f"extension {ext or '(none)'} → {ext_cat}"]
    if hinted:
        reasons.append(f"name hint → {hinted}")
    if sniffed and sniffed != OTHER_CATEGORY:
        reasons.append(f"magic bytes → {sniffed}")
    if mime:
        reasons.append(f"mime {mime} → {mime_cat}")
    reasons.append(f"final → {final}")
    return {
        "path": str(path),
        "extension": ext,
        "extension_category": ext_cat,
        "smart": hinted or None,
        "magic": sniffed if sniffed != OTHER_CATEGORY else None,
        "mime": mime,
        "mime_category": mime_cat if mime else None,
        "final": final,
        "why": reasons,
    }
