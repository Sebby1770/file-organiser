"""End-to-end coverage for desktop organise preview, apply, and undo."""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import file_organiser.app_server as app_server
from file_organiser.app_server import serve_app
from file_organiser.history import HistoryManager


def test_desktop_applies_exact_plan_and_undoes_exact_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "invoice.pdf"
    source.write_text("invoice", encoding="utf-8")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    threading.Thread(
        target=serve_app,
        kwargs={"host": "127.0.0.1", "port": port, "open_browser": False},
        daemon=True,
    ).start()
    host = f"127.0.0.1:{port}"

    def request(
        method: str,
        path: str,
        *,
        token: str = "",
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": host}
        body: str | None = None
        if token:
            headers["X-File-Organiser-Token"] = token
            headers["Origin"] = f"http://{host}"
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
    while True:
        try:
            status, page = request("GET", "/")
            if status == 200:
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    token_match = re.search(
        rb'name="file-organiser-session" content="([^"]+)"', page
    )
    assert token_match
    token = token_match.group(1).decode("ascii")

    status, _raw = request(
        "POST", "/api/organize/plan", payload={"path": str(tmp_path)}
    )
    assert status == 403
    status, _raw = request("GET", "/api/organize/status")
    assert status == 403

    status, raw = request(
        "POST", "/api/scan", token=token, payload={"path": str(tmp_path)}
    )
    assert status == 200 and json.loads(raw)["ok"]
    deadline = time.monotonic() + 5
    while True:
        status, raw = request("GET", "/api/status")
        scan_status = json.loads(raw)["status"]
        if scan_status == "done":
            break
        assert scan_status != "error"
        if time.monotonic() >= deadline:
            raise AssertionError("desktop scan did not finish")
        time.sleep(0.02)

    def create_plan() -> dict[str, Any]:
        response_status, response_raw = request(
            "POST",
            "/api/organize/plan",
            token=token,
            payload={
                "path": str(tmp_path),
                "profile": "standard",
                "recursive": False,
                "magic": False,
                "smart": False,
            },
        )
        assert response_status == 200
        return json.loads(response_raw)["plan"]

    stale_plan = create_plan()
    assert stale_plan["verification"] == "sha256"
    assert stale_plan["files"][0]["source_state"]["sha256"]

    # A guessed or old id cannot consume the server-held plan.
    status, raw = request(
        "POST",
        "/api/organize/apply",
        token=token,
        payload={"plan_id": "wrong-plan"},
    )
    assert status == 400 and "plan changed" in json.loads(raw)["error"]
    status, raw = request("GET", "/api/organize/status", token=token)
    assert status == 200
    assert json.loads(raw)["pending"]["plan_id"] == stale_plan["id"]

    # Applying the exact id still fails closed if a source changed after review.
    source.write_text("changed", encoding="utf-8")
    status, raw = request(
        "POST",
        "/api/organize/apply",
        token=token,
        payload={"plan_id": stale_plan["id"]},
    )
    failed = json.loads(raw)
    assert status == 400 and not failed["ok"]
    assert "changed since plan" in failed["error"]
    assert source.read_text(encoding="utf-8") == "changed"
    assert not (tmp_path / "Documents" / source.name).exists()

    plan = create_plan()
    real_execute = app_server.execute_plan
    entered = threading.Event()
    release = threading.Event()

    def slow_execute(*args: Any, **kwargs: Any):
        entered.set()
        assert release.wait(timeout=3)
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(app_server, "execute_plan", slow_execute)
    apply_response: list[tuple[int, bytes]] = []
    apply_thread = threading.Thread(
        target=lambda: apply_response.append(
            request(
                "POST",
                "/api/organize/apply",
                token=token,
                payload={"plan_id": plan["id"]},
            )
        )
    )
    apply_thread.start()
    assert entered.wait(timeout=3)

    # The threaded local server cannot scan, trash, or start another organiser
    # while the exact-plan transaction owns the filesystem-operation lease.
    status, raw = request(
        "POST", "/api/scan", token=token, payload={"path": str(tmp_path)}
    )
    assert status == 409 and "operation is running" in json.loads(raw)["error"]
    status, raw = request(
        "POST", "/api/trash", token=token, payload={"path": str(source)}
    )
    assert status == 409 and "operation is running" in json.loads(raw)["error"]
    release.set()
    apply_thread.join(timeout=3)
    assert not apply_thread.is_alive()
    status, raw = apply_response[0]
    applied = json.loads(raw)
    assert status == 200 and applied["ok"] and applied["completed"] == 1
    transaction_id = applied["transaction_id"]
    destination = tmp_path / "Documents" / source.name
    assert destination.read_text(encoding="utf-8") == "changed"

    snapshot = HistoryManager(tmp_path).peek()
    assert snapshot and snapshot["id"] == transaction_id
    result_state = snapshot["operations"][0]["result_state"]
    assert result_state["sha256"]

    # Undo status and execution also bind to the reviewed transaction id and
    # retain SHA-256 drift detection after apply.
    original_mtime = destination.stat().st_mtime_ns
    destination.write_text("altered", encoding="utf-8")
    os.utime(destination, ns=(original_mtime, original_mtime))
    status, raw = request("GET", "/api/organize/status", token=token)
    undo_status = json.loads(raw)["undo"]
    assert status == 200 and not undo_status["ready"]
    assert "changed since apply" in undo_status["error"]

    destination.write_text("changed", encoding="utf-8")
    os.utime(destination, ns=(original_mtime, original_mtime))
    status, raw = request(
        "POST",
        "/api/organize/undo",
        token=token,
        payload={"transaction_id": "wrong-transaction"},
    )
    assert status == 400 and "history changed" in json.loads(raw)["error"]
    assert destination.exists()

    status, raw = request(
        "POST",
        "/api/organize/undo",
        token=token,
        payload={"transaction_id": transaction_id},
    )
    undone = json.loads(raw)
    assert status == 200 and undone["ok"] and undone["completed"] == 1
    assert source.read_text(encoding="utf-8") == "changed"
    assert not destination.exists()
    assert HistoryManager(tmp_path).peek() is None
