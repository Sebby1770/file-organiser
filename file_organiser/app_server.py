"""Desktop app: local HTTP UI over the disk map, plus organize / trash / reveal."""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .disk import candidate_roots, scan_usage, text_map
from .duplicates import choose_keeper, find_duplicates, reclaimable_bytes
from .explain import explain_path
from .organizer import build_preview_plan
from .rules import load_rules
from .scanner import format_size
from .safety import dangerous_target

STATIC = Path(__file__).resolve().parent / "static"


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.root: Path | None = None
        self.result: dict[str, Any] | None = None
        self.status = "idle"
        self.visited = 0
        self.bytes = 0
        self.error = ""
        self.rules = load_rules(None)

    def allowed(self, path: Path) -> bool:
        if self.root is None:
            return False
        try:
            path.resolve().relative_to(self.root.resolve())
            return True
        except (ValueError, OSError):
            return path.resolve() == self.root.resolve()


STATE = AppState()


def _trash(path: Path) -> str:
    try:
        from send2trash import send2trash

        send2trash(str(path))
        return "trashed"
    except Exception:
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return "deleted"


def _reveal(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])


def serve_app(host: str = "127.0.0.1", port: int = 8765, folder: Path | None = None, open_browser: bool = True) -> None:
    if folder:
        STATE.root = folder.expanduser().resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"FileOrganiserApp/{__version__}"

        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("[app] " + (fmt % args) + "\n")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            qs = {k: v[0] if v else "" for k, v in parse_qs(parsed.query).items()}
            if path == "/api/health":
                self._json({"ok": True, "version": __version__, "name": "File Organiser"})
                return
            if path == "/api/roots":
                self._json({"ok": True, "roots": candidate_roots()})
                return
            if path == "/api/status":
                with STATE.lock:
                    self._json(
                        {
                            "ok": True,
                            "status": STATE.status,
                            "visited": STATE.visited,
                            "bytes": STATE.bytes,
                            "label": format_size(STATE.bytes),
                            "root": str(STATE.root) if STATE.root else None,
                            "error": STATE.error,
                        }
                    )
                return
            if path == "/api/scan":
                with STATE.lock:
                    self._json({"ok": True, "result": STATE.result, "status": STATE.status, "error": STATE.error})
                return
            if path == "/api/why":
                target = Path(qs.get("path") or "")
                if not target.exists():
                    self._json({"error": "missing path"}, 400)
                    return
                self._json({"ok": True, "why": explain_path(target, STATE.rules)})
                return
            self._static(path)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(min(length, 2_000_000)) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return
            route = parsed.path
            if route == "/api/scan":
                self._json(self._start_scan(body))
                return
            if route == "/api/trash":
                self._json(self._do_trash(body))
                return
            if route == "/api/reveal":
                target = Path(str(body.get("path") or ""))
                if not target.exists():
                    self._json({"error": "missing path"}, 400)
                    return
                _reveal(target)
                self._json({"ok": True})
                return
            if route == "/api/organize":
                self._json(self._do_organize(body))
                return
            if route == "/api/dupes":
                self._json(self._do_dupes(body))
                return
            self._json({"error": "not found"}, 404)

        def _start_scan(self, body: dict[str, Any]) -> dict[str, Any]:
            raw = str(body.get("path") or "").strip()
            if not raw:
                return {"ok": False, "error": "path required"}
            folder = Path(raw).expanduser()
            try:
                folder = folder.resolve()
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            if not folder.is_dir():
                return {"ok": False, "error": "not a directory"}
            with STATE.lock:
                if STATE.status == "running":
                    return {"ok": False, "error": "scan already running"}
                STATE.status = "running"
                STATE.root = folder
                STATE.result = None
                STATE.error = ""
                STATE.visited = 0
                STATE.bytes = 0

            def job() -> None:
                try:
                    def prog(n: int, b: int) -> None:
                        with STATE.lock:
                            STATE.visited = n
                            STATE.bytes = b

                    result = scan_usage(folder, rules=STATE.rules, progress=prog)
                    with STATE.lock:
                        STATE.result = result
                        STATE.status = "done"
                        STATE.visited = result["visited"]
                        STATE.bytes = result["size"]
                except Exception as exc:  # noqa: BLE001
                    with STATE.lock:
                        STATE.status = "error"
                        STATE.error = str(exc)

            threading.Thread(target=job, daemon=True).start()
            return {"ok": True, "status": "running", "root": str(folder)}

        def _do_trash(self, body: dict[str, Any]) -> dict[str, Any]:
            target = Path(str(body.get("path") or "")).expanduser()
            try:
                target = target.resolve()
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            if not STATE.allowed(target):
                return {"ok": False, "error": "path is outside the scanned folder"}
            if dangerous_target(target) and not body.get("force"):
                return {"ok": False, "error": dangerous_target(target)}
            if not target.exists():
                return {"ok": False, "error": "already gone"}
            action = _trash(target)
            return {"ok": True, "action": action, "path": str(target)}

        def _do_organize(self, body: dict[str, Any]) -> dict[str, Any]:
            folder = Path(str(body.get("path") or (STATE.root or ""))).expanduser()
            if not folder.is_dir():
                return {"ok": False, "error": "not a directory"}
            danger = dangerous_target(folder)
            if danger and not body.get("force"):
                return {"ok": False, "error": danger}
            plan = build_preview_plan(
                folder,
                STATE.rules,
                recursive=bool(body.get("recursive", True)),
                use_magic=bool(body.get("magic", True)),
                use_smart=bool(body.get("smart", True)),
            )
            return {"ok": True, "plan": plan}

        def _do_dupes(self, body: dict[str, Any]) -> dict[str, Any]:
            folder = Path(str(body.get("path") or (STATE.root or ""))).expanduser()
            if not folder.is_dir():
                return {"ok": False, "error": "not a directory"}
            min_size = int(body.get("min_size") or 256_000)
            groups = find_duplicates(folder, recursive=True, min_size=min_size, show_progress=False, use_cache=True)
            payload = []
            for digest, paths in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:40]:
                keeper = choose_keeper(paths, keep="largest")
                payload.append(
                    {
                        "hash": digest[:16],
                        "count": len(paths),
                        "keep": str(keeper),
                        "files": [{"path": str(p), "name": p.name, "label": format_size(p.stat().st_size) if p.exists() else "?"} for p in paths],
                    }
                )
            reclaim, n = reclaimable_bytes(groups, keep="largest")
            return {
                "ok": True,
                "groups": payload,
                "reclaim": reclaim,
                "reclaim_label": format_size(reclaim),
                "extra_files": n,
            }

        def _static(self, url_path: str) -> None:
            rel = "index.html" if url_path in {"", "/"} else url_path.lstrip("/")
            if rel.endswith("/"):
                rel += "index.html"
            target = (STATIC / rel).resolve()
            try:
                target.relative_to(STATIC.resolve())
            except ValueError:
                self._bytes(b"forbidden", "text/plain", 403)
                return
            if not target.is_file():
                self._bytes(b"not found", "text/plain", 404)
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self._bytes(target.read_bytes(), ctype)

        def _json(self, payload: Any, status: int = 200) -> None:
            if isinstance(payload, dict) and payload.get("ok") is False:
                status = 400
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"File Organiser {__version__}  {url}")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()


def launch(host: str = "127.0.0.1", port: int = 8765, folder: Path | None = None) -> None:
    serve_app(host, port, folder, open_browser=True)


def map_text(folder: Path) -> str:
    result = scan_usage(folder)
    return text_map(result)
