"""Static integrity checks for the GitHub Pages product site."""

from __future__ import annotations

import json
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.h1 = 0
        self.metas: dict[tuple[str, str], str] = {}
        self.references: list[tuple[str, str]] = []
        self.images: list[dict[str, str | None]] = []
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._json_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1 += 1
        elif tag == "meta":
            if values.get("name"):
                self.metas[("name", str(values["name"]).lower())] = str(values.get("content") or "")
            if values.get("property"):
                self.metas[("property", str(values["property"]).lower())] = str(values.get("content") or "")
        elif tag in {"a", "link"} and values.get("href"):
            self.references.append(("href", str(values["href"])))
        elif tag in {"script", "img"} and values.get("src"):
            self.references.append(("src", str(values["src"])))
        if tag == "img":
            self.images.append(values)
        if tag == "script" and values.get("type") == "application/ld+json":
            self._in_json_ld = True
            self._json_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_buffer))
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_json_ld:
            self._json_buffer.append(data)


def _local_target(page: Path, value: str, root: Path) -> Path | None:
    if value.startswith(("#", "mailto:", "tel:", "data:")):
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return None
    path = parsed.path
    if not path:
        return None
    target = (page.parent / path).resolve()
    if path.endswith("/") or target.is_dir():
        target = target / "index.html"
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return Path("/__outside_site__")
    return target


def check_site(root: Path) -> list[str]:
    errors: list[str] = []
    pages: dict[Path, PageParser] = {}
    titles: dict[str, Path] = {}
    descriptions: dict[str, Path] = {}
    for page in sorted(root.glob("*.html")):
        parser = PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        pages[page] = parser
        label = page.name
        title = parser.title.strip()
        description = parser.metas.get(("name", "description"), "").strip()
        if not title:
            errors.append(f"{label}: missing title")
        elif title in titles:
            errors.append(f"{label}: title duplicates {titles[title].name}")
        else:
            titles[title] = page
        if not description:
            errors.append(f"{label}: missing meta description")
        elif description in descriptions:
            errors.append(f"{label}: description duplicates {descriptions[description].name}")
        else:
            descriptions[description] = page
        if parser.h1 != 1:
            errors.append(f"{label}: expected one h1, found {parser.h1}")
        for image in parser.images:
            if "alt" not in image:
                errors.append(f"{label}: image lacks alt attribute: {image.get('src')}")
        for _kind, reference in parser.references:
            target = _local_target(page, reference, root)
            if target is not None and not target.exists():
                errors.append(f"{label}: broken local reference {reference}")
        for raw in parser.json_ld:
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{label}: invalid JSON-LD: {exc}")

        robots = parser.metas.get(("name", "robots"), "").lower()
        if "noindex" not in robots:
            required = [
                ("property", "og:title"),
                ("property", "og:description"),
                ("property", "og:image"),
                ("property", "og:image:alt"),
                ("name", "twitter:card"),
                ("name", "twitter:title"),
                ("name", "twitter:description"),
                ("name", "twitter:image"),
                ("name", "twitter:image:alt"),
            ]
            for key in required:
                if not parser.metas.get(key):
                    errors.append(f"{label}: missing social metadata {key[1]}")
            canonical = any(kind == "href" and value.startswith("https://sebby1770.github.io/file-organiser") for kind, value in parser.references)
            if not canonical:
                errors.append(f"{label}: missing absolute canonical")

    for page in pages:
        text = page.read_text(encoding="utf-8")
        if "github.com/Sebby1770/file-organiser/releases/latest" in text:
            errors.append(f"{page.name}: contains a broken latest-release CTA")

    social = root / "og.png"
    if not social.is_file():
        errors.append("missing og.png")
    else:
        data = social.read_bytes()[:24]
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            errors.append("og.png is not a PNG")
        else:
            width, height = struct.unpack(">II", data[16:24])
            if (width, height) != (1731, 909):
                errors.append(f"og.png dimensions are {width}x{height}, expected 1731x909")
    for required in ("robots.txt", "sitemap.xml", "404.html", "privacy.html", "thanks.html", "maker-avatar.png"):
        if not (root / required).is_file():
            errors.append(f"missing {required}")
    return errors


if __name__ == "__main__":
    site_root = Path(__file__).resolve().parents[1] / "docs"
    failures = check_site(site_root)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        raise SystemExit(1)
    print(f"Site checks passed for {len(list(site_root.glob('*.html')))} HTML pages.")
