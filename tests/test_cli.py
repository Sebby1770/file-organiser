from __future__ import annotations

import json
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
