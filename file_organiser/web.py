"""Local studio: serve the GitHub Pages site and, optionally, a live preview API."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .organizer import build_preview_plan
from .rules import load_rules

DOCS = Path(__file__).resolve().parents[1] / "docs"


def serve(host: str = "127.0.0.1", port: int = 8765, folder: Path | None = None) -> None:
    rules = load_rules(None)
    target = folder.resolve() if folder else None

    class Handler(BaseHTTPRequestHandler):
        server_version = f"FileOrganiser/{__version__}"

        def log_message(self, fmt: str, *args: object) -> None:
            print("[studio] " + (fmt % args))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self._json({"ok": True, "version": __version__, "folder": str(target) if target else None})
                return
            if parsed.path == "/api/preview":
                if target is None:
                    self._json({"error": "pass --folder for a live preview"}, 400)
                    return
                qs = parse_qs(parsed.query)
                recursive = (qs.get("recursive") or ["0"])[0] in {"1", "true", "yes"}
                magic = (qs.get("magic") or ["1"])[0] in {"1", "true", "yes"}
                smart = (qs.get("smart") or ["1"])[0] in {"1", "true", "yes"}
                plan = build_preview_plan(
                    target,
                    rules,
                    recursive=recursive,
                    use_magic=magic,
                    use_smart=smart,
                )
                self._json({"ok": True, "plan": plan})
                return
            rel = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
            path = (DOCS / rel).resolve()
            try:
                path.relative_to(DOCS.resolve())
            except ValueError:
                self._bytes(b"forbidden", "text/plain", 403)
                return
            if not path.is_file():
                self._bytes(b"not found", "text/plain", 404)
                return
            ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self._bytes(path.read_bytes(), ctype)

        def _json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"file-organiser studio {__version__}  http://{host}:{port}")
    if target:
        print(f"live preview of {target}")
    httpd.serve_forever()
