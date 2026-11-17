"""Build the offline product ZIP from the same report view used by the public demo."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "docs" / "review-kit"


def example(day, images, videos):
    return json.dumps({
        "schema": "file-organiser.storage-snapshot.v1", "source": "browser",
        "captured": f"2026-04-{day:02d}T12:00:00Z", "errors": 0,
        "groups": [{"type": "Images", "bytes": images * 1024**3, "files": images * 12},
                   {"type": "Videos", "bytes": videos * 1024**3, "files": videos}],
    }, indent=2) + "\n"


def build(destination):
    template = (VIEW / "index.html").read_text(encoding="utf-8")
    for section in ("demo-meta", "demo-offer"):
        start = template.index(f"<!-- {section}-start -->")
        closing = f"<!-- {section}-end -->"
        end = template.index(closing, start) + len(closing)
        template = template[:start] + template[end:]
    css = (VIEW / "review.css").read_text(encoding="utf-8")
    js = (VIEW / "review.js").read_text(encoding="utf-8")
    script_hash = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
    policy = (f"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; "
              "img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'")
    page = (template.replace('<meta charset="utf-8">', f'<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="{policy}">')
            .replace('data-mode="demo"', 'data-mode="offline"')
            .replace('<link rel="stylesheet" href="review.css">', f"<style>{css}</style>")
            .replace('<script src="offer.js"></script>', '')
            .replace('<script src="review.js"></script>', f"<script>{js}</script>"))
    entries = {
        "Storage Review Kit.html": page, "START HERE.md": (ROOT / "review-kit/START-HERE.md").read_text(encoding="utf-8"),
        "LICENSE.txt": (ROOT / "LICENSE").read_text(encoding="utf-8"),
        "Examples/01-baseline.json": example(1, 16, 48),
        "Examples/02-current.json": example(8, 12, 60),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
        staged = Path(tmp.name)
    try:
        with ZipFile(staged, "w") as archive:
            for name, content in entries.items():
                info = ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content.encode("utf-8"))
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "Storage-Review-Kit.zip")
    print(build(parser.parse_args().output))
