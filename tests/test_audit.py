"""Clutter audit: scores, copy-like names, rule clashes, and the CLI."""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import file_organiser.app_server as app_server
from file_organiser.app_server import serve_app
from file_organiser.audit import (
    audit_folder,
    clutter_score,
    extension_conflicts,
    format_audit,
    normalize_stem,
    tidy_preview,
)
from file_organiser.cli import main
from file_organiser.rules import DEFAULT_RULES


def test_normalize_stem_collapses_copy_markers():
    assert normalize_stem("Report.pdf") == "report"
    assert normalize_stem("Report (1).pdf") == "report"
    assert normalize_stem("Report copy.pdf") == "report"
    assert normalize_stem("Report_final.pdf") == "report"
    assert normalize_stem("Report (copy).pdf") == "report"
    assert normalize_stem("copy of report.pdf") == "copy of report"


def test_extension_conflicts_follow_first_seen_categories():
    clashes = extension_conflicts(
        {"Docs": [".pdf", "txt"], "Papers": [".PDF"], "Notes": [".txt"]}
    )
    assert clashes == [
        {"extension": ".pdf", "categories": ["Docs", "Papers"]},
        {"extension": ".txt", "categories": ["Docs", "Notes"]},
    ]
    assert extension_conflicts(DEFAULT_RULES) == []


def test_audit_folder_scores_loose_copies_without_following_links(tmp_path: Path):
    (tmp_path / "Report.pdf").write_text("a", encoding="utf-8")
    (tmp_path / "Report (1).pdf").write_text("b", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("c", encoding="utf-8")
    nested = tmp_path / "keep"
    nested.mkdir()
    (nested / "photo.png").write_bytes(b"png")
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)

    report = audit_folder(
        tmp_path,
        {"Documents": [".pdf", ".txt"], "Images": [".png"]},
    )
    names = {item["rel"] for item in report["loose"]}
    assert names == {"Report.pdf", "Report (1).pdf", "notes.txt", "empty.txt"}
    assert report["movable_count"] == 4
    assert report["twins"][0]["count"] == 2
    assert report["twins"][0]["stem"] == "report"
    assert "secret.txt" not in json.dumps(report)
    assert report["score"] == clutter_score(
        files=report["files"],
        loose_count=4,
        twin_groups=1,
        other_count=0,
        empty_count=1,
    )
    assert "does not delete" in format_audit(report)
    text = format_audit(report)
    assert "Report.pdf" in text
    assert "secret.txt" not in text


def test_audit_cli_json(tmp_path: Path, capsys):
    (tmp_path / "solo.md").write_text("hello", encoding="utf-8")
    assert main(["audit", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["files"] == 1
    assert payload["loose_count"] == 1
    assert payload["grade"] in {"Tidy", "Mixed", "Cluttered"}


def test_audit_cli_missing_folder(tmp_path: Path):
    missing = tmp_path / "nope"
    assert main(["audit", str(missing)]) == 1


def test_tidy_preview_lists_root_files_without_moving(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("a", encoding="utf-8")
    nested = tmp_path / "keep"
    nested.mkdir()
    (nested / "photo.png").write_bytes(b"x")
    (tmp_path / "mystery.bin").write_bytes(b"z")
    rules = {"Documents": [".txt"], "Images": [".png"]}
    moves = tidy_preview(tmp_path, rules)
    assert [item["rel_dest"] for item in moves] == ["Documents/notes.txt"]
    assert (tmp_path / "notes.txt").is_file()
    assert not (tmp_path / "Documents").exists()
    report = audit_folder(tmp_path, rules, preview=True)
    assert report["preview"] == moves
    plain = audit_folder(tmp_path, rules)
    assert plain["preview"] == []


def test_stale_loose_files_lower_the_score(tmp_path: Path):
    old = tmp_path / "old.txt"
    old.write_text("a", encoding="utf-8")
    old_time = time.time() - (200 * 86400)
    os.utime(old, (old_time, old_time))
    report = audit_folder(tmp_path, {"Documents": [".txt"]})
    assert report["stale_loose"] == 1
    fresh = clutter_score(
        files=1,
        loose_count=1,
        twin_groups=0,
        other_count=0,
        empty_count=0,
        stale_loose=0,
    )
    assert report["score"] == fresh - 2


def test_empty_folder_is_tidy(tmp_path: Path):
    report = audit_folder(tmp_path, {})
    assert report["score"] == 100
    assert report["grade"] == "Tidy"
    assert report["files"] == 0


def test_desktop_audit_route_is_read_only(tmp_path: Path):
    (tmp_path / "Report.pdf").write_text("a", encoding="utf-8")
    (tmp_path / "Report (1).pdf").write_text("b", encoding="utf-8")
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (outside / "other.txt").write_text("no", encoding="utf-8")

    with app_server.STATE.lock:
        app_server.STATE.status = "idle"
        app_server.STATE.organize_busy = False

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    threading.Thread(
        target=serve_app,
        kwargs={
            "host": "127.0.0.1",
            "port": port,
            "folder": tmp_path,
            "open_browser": False,
        },
        daemon=True,
    ).start()

    def request(method: str, path: str, token: str = "", payload: dict | None = None):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}"}
        body = None
        if token:
            headers["X-File-Organiser-Token"] = token
            headers["Origin"] = f"http://127.0.0.1:{port}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, data

    deadline = time.monotonic() + 3
    page = b""
    while True:
        try:
            status, page = request("GET", "/")
            if status == 200 and b"tab-clutter" in page:
                break
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise AssertionError("desktop app did not serve the clutter tab")
        time.sleep(0.02)

    token = re.search(rb'name="file-organiser-session" content="([^"]+)"', page)
    assert token
    session = token.group(1).decode("ascii")
    before = sorted(path.name for path in tmp_path.iterdir())

    status, raw = request(
        "POST",
        "/api/audit",
        session,
        {"path": str(tmp_path), "preview": True},
    )
    assert status == 200
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["audit"]["twins"][0]["count"] == 2
    assert payload["audit"]["preview"]
    assert all(item["rel_dest"].startswith("Documents/") for item in payload["audit"]["preview"])
    assert sorted(path.name for path in tmp_path.iterdir()) == before

    status, raw = request(
        "POST",
        "/api/audit",
        session,
        {"path": str(outside)},
    )
    assert status == 400
    denied = json.loads(raw)
    assert denied["ok"] is False
    assert "outside" in denied["error"]
    for child in outside.iterdir():
        child.unlink()
    outside.rmdir()
