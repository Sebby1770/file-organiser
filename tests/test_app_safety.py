import json
import http.client
import re
import socket
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from file_organiser.app_server import (
    _host_header_loopback,
    _loopback_host,
    _origin_allowed,
    _safe_desktop_delete,
    _trash,
    serve_app,
)
from file_organiser.scan_report import build_scan_report, report_csv, report_json


def test_trash_failure_never_falls_back_to_permanent_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "keep.txt"
    target.write_text("important", encoding="utf-8")

    def fail(_path: str) -> None:
        raise RuntimeError("platform trash unavailable")

    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(send2trash=fail))
    with pytest.raises(OSError, match="nothing was deleted"):
        _trash(target)
    assert target.read_text(encoding="utf-8") == "important"


def test_desktop_delete_policy_locks_keep_and_review(tmp_path: Path):
    safe = tmp_path / ".DS_Store"
    review = tmp_path / "notes.txt"
    protected = tmp_path / ".ssh" / "id_ed25519"
    protected.parent.mkdir()
    for path in (safe, review, protected):
        path.write_text("x", encoding="utf-8")
    advice = {
        "delete": [{"path": str(safe)}],
        "review": [{"path": str(review)}],
        "keep": [{"path": str(protected)}],
    }

    assert _safe_desktop_delete(safe, advice)[0]
    assert not _safe_desktop_delete(review, advice)[0]
    assert not _safe_desktop_delete(protected, advice)[0]


def test_local_server_guards():
    assert _loopback_host("127.0.0.1")
    assert _loopback_host("::1")
    assert _loopback_host("localhost")
    assert not _loopback_host("0.0.0.0")
    assert _host_header_loopback("127.0.0.1:8765")
    assert _host_header_loopback("[::1]:8765")
    assert _host_header_loopback("localhost:8765")
    assert not _host_header_loopback("attacker.example:8765")
    assert not _host_header_loopback("localhost.attacker.example:8765")
    assert _origin_allowed(None, "127.0.0.1:8765")
    assert _origin_allowed("http://127.0.0.1:8765", "127.0.0.1:8765")
    assert not _origin_allowed("https://attacker.example", "127.0.0.1:8765")
    assert not _origin_allowed("http://attacker.example:8765", "attacker.example:8765")


def test_scan_report_exports_paths_only_on_explicit_build():
    result = {"root": "/tmp/example", "files": 2, "size": 12}
    advice = {
        "summary": {"delete_n": 1},
        "delete": [
            {
                "name": "<img src=x onerror=alert(1)>",
                "path": "/tmp/example/<img src=x onerror=alert(1)>",
                "size": 12,
                "label": "12 B",
                "kind": "junk",
                "confidence": "high",
                "reason": "test",
            }
        ],
        "review": [],
        "keep": [],
    }
    payload = build_scan_report(result, advice)
    decoded = json.loads(report_json(payload))
    assert decoded["schema"] == "file-organiser.scan-report.v1"
    assert decoded["items"][0]["name"].startswith("<img")
    csv_text = report_csv(payload).decode("utf-8")
    assert "bucket,name,path" in csv_text


def test_desktop_ui_escapes_filesystem_strings_and_uses_session_token():
    js = (Path(__file__).parents[1] / "file_organiser" / "static" / "app.js").read_text(encoding="utf-8")
    html = (Path(__file__).parents[1] / "file_organiser" / "static" / "index.html").read_text(encoding="utf-8")
    assert "function esc(value)" in js
    assert "esc(item.name)" in js
    assert "data-paths='${JSON.stringify" not in js
    assert 'headers.set("X-File-Organiser-Token"' in js
    assert "__FILE_ORGANISER_TOKEN__" in html


def test_live_local_server_rejects_rebinding_and_unauthorized_posts():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    threading.Thread(
        target=serve_app,
        kwargs={"host": "127.0.0.1", "port": port, "open_browser": False},
        daemon=True,
    ).start()

    def request(method: str, path: str, *, host: str, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        merged = {"Host": host, **(headers or {})}
        connection.request(method, path, body=body, headers=merged)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    deadline = time.monotonic() + 3
    while True:
        try:
            status, page = request("GET", "/", host=f"127.0.0.1:{port}")
            if status == 200:
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)

    token_match = re.search(rb'name="file-organiser-session" content="([^"]+)"', page)
    assert token_match
    token = token_match.group(1).decode("ascii")
    status, _ = request("GET", "/", host=f"attacker.example:{port}")
    assert status == 421
    status, _ = request(
        "POST",
        "/api/cancel",
        host=f"127.0.0.1:{port}",
        headers={"Content-Type": "application/json"},
        body="{}",
    )
    assert status == 403
    status, _ = request(
        "POST",
        "/api/cancel",
        host=f"127.0.0.1:{port}",
        headers={
            "Content-Type": "application/json",
            "X-File-Organiser-Token": token,
            "Origin": "https://attacker.example",
        },
        body="{}",
    )
    assert status == 403
    status, _ = request(
        "POST",
        "/api/cancel",
        host=f"127.0.0.1:{port}",
        headers={
            "Content-Type": "application/json",
            "X-File-Organiser-Token": token,
            "Origin": f"http://127.0.0.1:{port}",
        },
        body="{}",
    )
    assert status == 200
