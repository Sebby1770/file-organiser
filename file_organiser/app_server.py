"""Desktop app: local HTTP UI over the disk map, plus organize / trash / reveal."""

from __future__ import annotations

import json
import ipaddress
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .advise import build_advice, is_protected, is_safe_delete
from .disk import candidate_roots, prune_tree, scan_usage, text_map
from .duplicates import choose_keeper, file_sha256, find_duplicates, reclaimable_bytes
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
        self.cancel = False
        self.maps = None
        self.advice: dict[str, Any] | None = None
        self.duplicate_groups: dict[str, tuple[str, tuple[Path, ...]]] = {}
        # Every app launch gets a fresh bearer token. It is injected into the
        # local UI and required for every mutating request, which prevents a
        # random website from driving this localhost service through CSRF.
        self.session_token = secrets.token_urlsafe(32)

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
    """Move *path* to the OS Trash, failing closed if that is unavailable.

    The desktop UI promises a recoverable Trash action. Falling back to
    ``unlink`` would turn a recoverable action into permanent deletion, so this
    helper deliberately never performs a direct delete.
    """
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise OSError("Trash is unavailable; nothing was deleted") from exc
    try:
        send2trash(str(path))
    except Exception as exc:  # send2trash uses platform-specific exception types
        raise OSError("Could not move this item to Trash; nothing was deleted") from exc
    return "trashed"


def _loopback_host(host: str) -> bool:
    """Return whether *host* is explicitly local-only."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin_allowed(origin: str | None, host_header: str) -> bool:
    """Accept no-Origin native requests or this server's exact HTTP origin."""
    return _host_header_loopback(host_header) and (not origin or origin == f"http://{host_header}")


def _host_header_loopback(host_header: str) -> bool:
    """Reject DNS-rebinding Host values even when they resolve to loopback."""
    if not host_header or "@" in host_header:
        return False
    try:
        parsed = urlparse(f"//{host_header}")
        hostname = parsed.hostname or ""
        # Accessing .port validates malformed or out-of-range ports.
        _ = parsed.port
    except ValueError:
        return False
    return _loopback_host(hostname)


def _safe_desktop_delete(path: Path, advice: dict[str, Any] | None) -> tuple[bool, str]:
    """Central deletion policy shared by every desktop-app delete route."""
    if is_protected(path) or dangerous_target(path):
        return False, "protected items are locked"
    safe, reason = is_safe_delete(path, advice)
    if not safe:
        return False, reason or "only items in Delete can be trashed here"
    return True, "advised delete"


def _pick_folder() -> str | None:
    """Native folder dialog. Returns a path or None if cancelled."""
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(
                ["osascript", "-e", "POSIX path of (choose folder)"],
                stderr=subprocess.DEVNULL,
            )
            return out.decode().strip()
        if sys.platform.startswith("win"):
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }",
                ]
            )
            text = out.decode().strip()
            return text or None
        out = subprocess.check_output(["zenity", "--file-selection", "--directory"], stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            chosen = filedialog.askdirectory()
            root.destroy()
            return chosen or None
        except Exception:
            return None


def _reveal(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])


def serve_app(host: str = "127.0.0.1", port: int = 8765, folder: Path | None = None, open_browser: bool = True) -> None:
    if not _loopback_host(host):
        raise ValueError("the desktop app may only bind to a loopback address")
    if folder:
        STATE.root = folder.expanduser().resolve()
    STATE.session_token = secrets.token_urlsafe(32)
    STATE.duplicate_groups = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"FileOrganiserApp/{__version__}"

        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("[app] " + (fmt % args) + "\n")

        def _request_authorized(self) -> bool:
            supplied = self.headers.get("X-File-Organiser-Token", "")
            if not supplied or not secrets.compare_digest(supplied, STATE.session_token):
                return False
            # Native helpers and automated tests do not always send Origin;
            # possession of the unguessable session token is still required.
            return _origin_allowed(self.headers.get("Origin"), self.headers.get("Host", ""))

        def _valid_host(self) -> bool:
            return _host_header_loopback(self.headers.get("Host", ""))

        def do_OPTIONS(self) -> None:  # noqa: N802
            # No cross-origin API is exposed by this local desktop service.
            if not self._valid_host():
                self._json({"ok": False, "error": "invalid local host"}, 421)
                return
            self._json({"ok": False, "error": "cross-origin requests are not allowed"}, 403)

        def do_GET(self) -> None:  # noqa: N802
            if not self._valid_host():
                self._json({"ok": False, "error": "invalid local host"}, 421)
                return
            parsed = urlparse(self.path)
            path = parsed.path
            qs = {k: v[0] if v else "" for k, v in parse_qs(parsed.query).items()}
            if path == "/api/health":
                home = str(Path.home())
                self._json(
                    {
                        "ok": True,
                        "version": __version__,
                        "name": "File Organiser",
                        "product": True,
                        "advise": True,
                        "home": home,
                    }
                )
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
                    self._json(
                        {
                            "ok": True,
                            "result": STATE.result,
                            "status": STATE.status,
                            "error": STATE.error,
                            "advice": STATE.advice,
                        }
                    )
                return
            if path == "/api/why":
                target = Path(qs.get("path") or "").expanduser()
                try:
                    target = target.resolve()
                except OSError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                if not STATE.allowed(target) or not target.exists():
                    self._json({"error": "missing path"}, 400)
                    return
                self._json({"ok": True, "why": explain_path(target, STATE.rules)})
                return
            if path == "/api/advise":
                with STATE.lock:
                    if STATE.advice is None and STATE.result:
                        STATE.advice = build_advice(STATE.result)
                    self._json({"ok": True, "advice": STATE.advice, "status": STATE.status})
                return
            if path == "/api/report":
                if not self._request_authorized():
                    self._json({"ok": False, "error": "invalid local app session"}, 403)
                    return
                with STATE.lock:
                    result = STATE.result
                    advice = STATE.advice
                if not result or not advice:
                    self._json({"ok": False, "error": "scan a folder first"})
                    return
                from .scan_report import build_scan_report, report_csv, report_json

                payload = build_scan_report(result, advice)
                report_format = qs.get("format", "json").lower()
                if report_format == "csv":
                    self._download(report_csv(payload), "text/csv; charset=utf-8", "file-organiser-scan.csv")
                elif report_format == "json":
                    self._download(report_json(payload), "application/json; charset=utf-8", "file-organiser-scan.json")
                else:
                    self._json({"ok": False, "error": "format must be json or csv"})
                return
            self._static(path)

        def do_POST(self) -> None:  # noqa: N802
            if not self._valid_host():
                self._json({"ok": False, "error": "invalid local host"}, 421)
                return
            if not self._request_authorized():
                self._json({"ok": False, "error": "invalid local app session"}, 403)
                return
            parsed = urlparse(self.path)
            if self.headers.get_content_type() != "application/json":
                self._json({"ok": False, "error": "Content-Type must be application/json"}, 415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                self._json({"ok": False, "error": "invalid Content-Length"}, 400)
                return
            if length < 0 or length > 2_000_000:
                self._json({"ok": False, "error": "request body too large"}, 413)
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "error": "JSON body must be an object"}, 400)
                return
            route = parsed.path
            if route == "/api/scan":
                self._json(self._start_scan(body))
                return
            if route == "/api/trash":
                self._json(self._do_trash(body))
                return
            if route == "/api/reveal":
                target = Path(str(body.get("path") or "")).expanduser()
                try:
                    target = target.resolve()
                except OSError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                if not STATE.allowed(target) or not target.exists():
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
            if route == "/api/dupes/purge":
                self._json(self._purge_dupes(body))
                return
            if route == "/api/zoom":
                self._json(self._zoom(body))
                return
            if route == "/api/pick":
                chosen = _pick_folder()
                if not chosen:
                    self._json({"ok": False, "error": "cancelled"})
                    return
                self._json({"ok": True, "path": chosen})
                return
            if route == "/api/cancel":
                with STATE.lock:
                    STATE.cancel = True
                self._json({"ok": True})
                return
            if route == "/api/advise":
                with STATE.lock:
                    if not STATE.result:
                        self._json({"ok": False, "error": "scan a folder first"})
                        return
                    STATE.advice = build_advice(STATE.result)
                    self._json({"ok": True, "advice": STATE.advice})
                return
            if route == "/api/advise/purge":
                self._json(self._purge_advice(body))
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
                STATE.cancel = False
                STATE.maps = None
                STATE.advice = None
                STATE.duplicate_groups = {}

            def job() -> None:
                try:
                    def prog(n: int, b: int) -> None:
                        with STATE.lock:
                            STATE.visited = n
                            STATE.bytes = b

                    result, maps = scan_usage(
                        folder,
                        rules=STATE.rules,
                        progress=prog,
                        cancel=lambda: STATE.cancel,
                    )
                    advice = build_advice(result)
                    with STATE.lock:
                        STATE.result = result
                        STATE.maps = maps
                        STATE.advice = advice
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
            safe, reason = _safe_desktop_delete(target, STATE.advice)
            if not safe:
                return {"ok": False, "error": reason}
            if not target.exists():
                return {"ok": False, "error": "already gone"}
            try:
                action = _trash(target)
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "action": action, "path": str(target)}

        def _do_organize(self, body: dict[str, Any]) -> dict[str, Any]:
            folder = Path(str(body.get("path") or (STATE.root or ""))).expanduser()
            if not folder.is_dir():
                return {"ok": False, "error": "not a directory"}
            try:
                folder = folder.resolve()
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            if not STATE.allowed(folder):
                return {"ok": False, "error": "scan this folder first"}
            danger = dangerous_target(folder)
            if danger:
                return {"ok": False, "error": danger}
            plan = build_preview_plan(
                folder,
                STATE.rules,
                recursive=bool(body.get("recursive", True)),
                use_magic=bool(body.get("magic", True)),
                use_smart=bool(body.get("smart", True)),
            )
            if body.get("apply"):
                from io import StringIO

                from rich.console import Console

                from .organizer import organize

                n = organize(
                    folder,
                    STATE.rules,
                    Console(file=StringIO()),
                    dry_run=False,
                    recursive=bool(body.get("recursive", True)),
                    use_magic=True,
                    use_smart=True,
                    force=False,
                )
                return {"ok": True, "plan": plan, "moved": n}
            return {"ok": True, "plan": plan}

        def _zoom(self, body: dict[str, Any]) -> dict[str, Any]:
            target = Path(str(body.get("path") or "")).expanduser()
            if not STATE.maps or not STATE.allowed(target) or not target.is_dir():
                return {"ok": False, "error": "scan this folder first"}
            tree = prune_tree(target, STATE.maps.dir_size, STATE.maps.dir_files, depth=0, rules=STATE.rules)
            tree["label"] = format_size(int(tree.get("size") or 0))
            return {"ok": True, "tree": tree}

        def _purge_advice(self, body: dict[str, Any]) -> dict[str, Any]:
            paths = [Path(str(p)) for p in (body.get("paths") or [])]
            if not paths:
                return {"ok": False, "error": "nothing selected"}
            done = []
            skipped = []
            with STATE.lock:
                advice = STATE.advice
            for raw in paths:
                try:
                    target = raw.expanduser().resolve()
                except OSError as exc:
                    skipped.append({"path": str(raw), "error": str(exc)})
                    continue
                if not STATE.allowed(target):
                    skipped.append({"path": str(target), "error": "outside the scanned folder"})
                    continue
                if is_protected(target) or dangerous_target(target):
                    skipped.append({"path": str(target), "error": "protected"})
                    continue
                ok, why = is_safe_delete(target, advice)
                if not ok:
                    skipped.append({"path": str(target), "error": why})
                    continue
                if not target.exists():
                    skipped.append({"path": str(target), "error": "already gone"})
                    continue
                try:
                    done.append({"path": str(target), "action": _trash(target)})
                except OSError as exc:
                    skipped.append({"path": str(target), "error": str(exc)})
            return {"ok": True, "n": len(done), "files": done, "skipped": skipped}

        def _purge_dupes(self, body: dict[str, Any]) -> dict[str, Any]:
            group_id = str(body.get("group") or "")
            known = STATE.duplicate_groups.get(group_id)
            if not known:
                return {"ok": False, "error": "duplicate group expired; scan duplicates again"}
            expected_digest, known_paths = known
            try:
                keep = Path(str(body.get("keep") or "")).expanduser().resolve()
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            if keep not in known_paths or not keep.is_file():
                return {"ok": False, "error": "the keeper is not in this duplicate group"}
            # Close the time-of-check/time-of-use gap: if any file changed since
            # preview, delete nothing and ask for a fresh duplicate scan.
            for path in known_paths:
                try:
                    if not path.is_file() or file_sha256(path) != expected_digest:
                        return {"ok": False, "error": "a file changed; scan duplicates again"}
                except OSError:
                    return {"ok": False, "error": "a file changed; scan duplicates again"}
            for path in known_paths:
                if path == keep:
                    continue
                safe, _reason = _safe_desktop_delete(path, STATE.advice)
                if not safe:
                    return {
                        "ok": False,
                        "error": "duplicate extras are Review items; reveal them and decide in your file manager",
                    }
            done = []
            skipped = []
            for path in known_paths:
                if path == keep:
                    continue
                if not STATE.allowed(path) or is_protected(path) or dangerous_target(path):
                    skipped.append({"path": str(path), "error": "protected"})
                    continue
                try:
                    done.append({"path": str(path), "action": _trash(path)})
                except OSError as exc:
                    skipped.append({"path": str(path), "error": str(exc)})
            STATE.duplicate_groups.pop(group_id, None)
            return {"ok": True, "n": len(done), "files": done, "skipped": skipped}

        def _do_dupes(self, body: dict[str, Any]) -> dict[str, Any]:
            folder = Path(str(body.get("path") or (STATE.root or ""))).expanduser()
            if not folder.is_dir():
                return {"ok": False, "error": "not a directory"}
            try:
                folder = folder.resolve()
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            if not STATE.allowed(folder):
                return {"ok": False, "error": "scan this folder first"}
            min_size = int(body.get("min_size") or 256_000)
            min_size = max(1, min(min_size, 10 * 1024 * 1024 * 1024))
            groups = find_duplicates(folder, recursive=True, min_size=min_size, show_progress=False, use_cache=True)
            payload = []
            STATE.duplicate_groups = {}
            for digest, paths in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:40]:
                resolved_paths = tuple(path.resolve() for path in paths)
                keeper = choose_keeper(list(resolved_paths), keep="largest")
                group_id = digest[:16]
                STATE.duplicate_groups[group_id] = (digest, resolved_paths)
                payload.append(
                    {
                        "group": group_id,
                        "count": len(resolved_paths),
                        "keep": str(keeper),
                        "files": [{"path": str(p), "name": p.name, "label": format_size(p.stat().st_size) if p.exists() else "?"} for p in resolved_paths],
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
            data = target.read_bytes()
            if target.name == "index.html":
                data = data.replace(b"__FILE_ORGANISER_TOKEN__", STATE.session_token.encode("ascii"))
            self._bytes(data, ctype)

        def _json(self, payload: Any, status: int = 200) -> None:
            if status == 200 and isinstance(payload, dict) and payload.get("ok") is False:
                status = 400
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if content_type.startswith("text/html"):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _download(self, data: bytes, content_type: str, filename: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
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
    result, _maps = scan_usage(folder)
    return text_map(result)
