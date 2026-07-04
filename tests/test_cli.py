from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from file_organizer.cli import main


def write(path: Path, contents: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def month_for(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m")


def test_cli_organize_recursive_partition_quarantine_and_undo(tmp_path: Path):
    photo = write(tmp_path / "photo.jpg")
    notes = write(tmp_path / "nested" / "notes.md")
    unknown = write(tmp_path / "mystery.blob")
    expected_month = month_for(photo)
    audit_log = tmp_path / ".organizer-events.jsonl"

    result = main(
        [
            "organize",
            str(tmp_path),
            "--recursive",
            "--partition-by-date",
            "--quarantine-unknown",
            "--json-log",
            str(audit_log),
        ]
    )

    assert result == 0
    assert (tmp_path / "Images" / expected_month / "photo.jpg").exists()
    assert (tmp_path / "Documents" / expected_month / "notes.md").exists()
    assert (tmp_path / "Quarantine" / expected_month / "mystery.blob").exists()
    assert audit_log.exists()
    first_event = json.loads(audit_log.read_text(encoding="utf-8").splitlines()[0])
    assert first_event["event"] == "move"

    undo_result = main(["undo", str(tmp_path)])

    assert undo_result == 0
    assert photo.exists()
    assert notes.exists()
    assert unknown.exists()


def test_cli_dry_run_does_not_move_files(tmp_path: Path):
    source = write(tmp_path / "budget.csv")

    result = main(["organize", str(tmp_path), "--dry-run", "--partition-by-date"])

    assert result == 0
    assert source.exists()
    assert not (tmp_path / "Spreadsheets").exists()


def test_max_files_limits_one_run(tmp_path: Path):
    write(tmp_path / "a.jpg")
    write(tmp_path / "b.jpg")

    result = main(["organize", str(tmp_path), "--max-files", "1"])

    assert result == 0
    assert len(list((tmp_path / "Images").glob("*.jpg"))) == 1


def test_invalid_max_files_is_rejected(tmp_path: Path):
    write(tmp_path / "a.jpg")

    result = main(["preview", str(tmp_path), "--max-files", "0"])

    assert result == 1


def test_dedupe_routes_duplicate_checksum_and_records_history(tmp_path: Path):
    first = write(tmp_path / "first.txt", "same")
    second = write(tmp_path / "second.txt", "same")
    db_path = tmp_path / ".runs.sqlite3"

    result = main(["organize", str(tmp_path), "--dedupe", "--db", str(db_path)])

    assert result == 0
    assert (tmp_path / "Documents" / first.name).exists()
    assert (tmp_path / "Duplicates" / second.name).exists()
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT planned, moved, errors FROM runs").fetchone()
    assert row == (2, 2, 0)


def test_history_command_reads_sqlite_runlog(tmp_path: Path):
    write(tmp_path / "report.pdf")
    db_path = tmp_path / ".runs.sqlite3"

    organize_result = main(["organize", str(tmp_path), "--db", str(db_path)])
    history_result = main(["history", str(tmp_path), "--db", str(db_path), "--limit", "5"])

    assert organize_result == 0
    assert history_result == 0


def test_manifest_command_writes_inventory(tmp_path: Path):
    write(tmp_path / "photo.jpg", "image")
    output = tmp_path / "manifest.json"

    result = main(["manifest", str(tmp_path), "--dedupe", "--output", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["total_files"] == 1
    assert payload["total_bytes"] == 5
    assert payload["category_bytes"]["Images"] == 5
    assert payload["files"][0]["category"] == "Images"
    assert payload["files"][0]["checksum"]


def test_manifest_can_redact_absolute_paths(tmp_path: Path):
    write(tmp_path / "nested" / "photo.jpg", "image")
    output = tmp_path / "manifest.json"

    result = main(["manifest", str(tmp_path), "--recursive", "--redact-paths", "--output", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    manifest_text = json.dumps(payload)
    assert result == 0
    assert payload["root"] == "[redacted]"
    assert payload["root_hash"]
    assert payload["files"][0]["source"] == str(Path("nested") / "photo.jpg")
    assert str(tmp_path) not in manifest_text


def test_manifest_supabase_sync_posts_inventory(tmp_path: Path, monkeypatch):
    calls = []

    class FakeResponse:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    monkeypatch.setenv("SUPABASE_TIMEOUT_SECONDS", "2")
    monkeypatch.setattr("file_organizer.organizer.urlopen", fake_urlopen)
    write(tmp_path / "photo.jpg", "image")
    output = tmp_path / "manifest.json"

    result = main(
        [
            "manifest",
            str(tmp_path),
            "--output",
            str(output),
            "--redact-paths",
            "--supabase-sync",
            "--supabase-table",
            "organizer_manifests",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    request, timeout = calls[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://example.supabase.co/rest/v1/organizer_manifests"
    assert timeout == 2
    assert body["file_count"] == 1
    assert body["total_bytes"] == 5
    assert body["category_counts"]["Images"] == 1
    assert body["manifest"]["root"] == "[redacted]"
    assert body["manifest"]["files"][0]["category"] == "Images"
    assert str(tmp_path) not in request.data.decode("utf-8")


def test_manifest_supabase_sync_requires_backend_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    write(tmp_path / "photo.jpg", "image")

    result = main(["manifest", str(tmp_path), "--supabase-sync"])

    assert result == 1


def test_min_age_skips_recent_files(tmp_path: Path):
    source = write(tmp_path / "fresh.txt")

    result = main(["organize", str(tmp_path), "--min-age-seconds", "3600"])

    assert result == 0
    assert source.exists()
    assert not (tmp_path / "Documents").exists()
