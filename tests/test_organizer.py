"""Tests for organize, unique naming, undo, and filters."""
from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from file_organiser.history import HISTORY_FILENAME, HistoryManager
from file_organiser.organizer import organize, unique_destination, undo
from file_organiser.rules import DEFAULT_RULES, load_rules
from file_organiser.scanner import parse_size, scan_folder


def _console() -> Console:
    return Console(force_terminal=False, quiet=True)


def test_parse_size():
    assert parse_size("1K") == 1024
    assert parse_size("10M") == 10 * 1024 * 1024
    assert parse_size("100") == 100
    assert parse_size("1.5K") == int(1.5 * 1024)


def test_unique_destination(tmp_path: Path):
    f = tmp_path / "photo.jpg"
    f.write_text("a", encoding="utf-8")
    dest = unique_destination(tmp_path / "photo.jpg")
    assert dest.name == "photo (1).jpg"
    dest.write_text("b", encoding="utf-8")
    dest2 = unique_destination(tmp_path / "photo.jpg")
    assert dest2.name == "photo (2).jpg"


def test_organize_and_undo_roundtrip(tmp_path: Path):
    (tmp_path / "pic.png").write_bytes(b"\x89PNG")
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "song.mp3").write_bytes(b"ID3")

    console = _console()
    n = organize(tmp_path, DEFAULT_RULES, console, quiet=True)
    assert n == 3
    assert (tmp_path / "Images" / "pic.png").exists()
    assert (tmp_path / "Documents" / "notes.txt").exists()
    assert (tmp_path / "Audio" / "song.mp3").exists()
    assert not (tmp_path / "pic.png").exists()
    assert (tmp_path / HISTORY_FILENAME).exists()

    restored = undo(tmp_path, console, quiet=True)
    assert restored == 3
    assert (tmp_path / "pic.png").exists()
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "song.mp3").exists()
    assert not (tmp_path / HISTORY_FILENAME).exists()


def test_organize_dry_run_no_changes(tmp_path: Path):
    (tmp_path / "a.pdf").write_text("pdf", encoding="utf-8")
    organize(tmp_path, DEFAULT_RULES, _console(), dry_run=True, quiet=True)
    assert (tmp_path / "a.pdf").exists()
    assert not (tmp_path / "Documents").exists()


def test_copy_mode(tmp_path: Path):
    (tmp_path / "a.pdf").write_text("pdf", encoding="utf-8")
    organize(tmp_path, DEFAULT_RULES, _console(), copy=True, quiet=True)
    assert (tmp_path / "a.pdf").exists()
    assert (tmp_path / "Documents" / "a.pdf").exists()

    undo(tmp_path, _console(), quiet=True)
    assert (tmp_path / "a.pdf").exists()
    assert not (tmp_path / "Documents" / "a.pdf").exists()


def test_by_date(tmp_path: Path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"jpg")
    import os
    os.utime(src, (1_700_000_000, 1_700_000_000))  # ~2023-11

    organize(tmp_path, DEFAULT_RULES, _console(), by_date=True, quiet=True)
    matches = list((tmp_path / "Images").rglob("photo.jpg"))
    assert len(matches) == 1
    # path should be Images/YYYY/MM/photo.jpg
    rel = matches[0].relative_to(tmp_path)
    parts = rel.parts
    assert parts[0] == "Images"
    assert len(parts) == 4
    assert parts[1].isdigit() and len(parts[1]) == 4
    assert parts[2].isdigit() and len(parts[2]) == 2


def test_min_size_filter(tmp_path: Path):
    small = tmp_path / "small.txt"
    large = tmp_path / "large.txt"
    small.write_text("x", encoding="utf-8")
    large.write_text("x" * 5000, encoding="utf-8")

    grouped = scan_folder(tmp_path, DEFAULT_RULES, min_size=1000)
    names = {p.name for files in grouped.values() for p in files}
    assert "large.txt" in names
    assert "small.txt" not in names


def test_exclude_glob(tmp_path: Path):
    (tmp_path / "keep.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "skip.tmp").write_text("tmp", encoding="utf-8")
    grouped = scan_folder(tmp_path, DEFAULT_RULES, exclude=["*.tmp"])
    names = {p.name for files in grouped.values() for p in files}
    assert "keep.py" in names
    assert "skip.tmp" not in names


def test_recursive_skips_category_folders(tmp_path: Path):
    (tmp_path / "loose.png").write_bytes(b"png")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "deep.pdf").write_text("pdf", encoding="utf-8")
    # Pretend already organized
    images = tmp_path / "Images"
    images.mkdir()
    (images / "already.jpg").write_bytes(b"jpg")

    grouped = scan_folder(tmp_path, DEFAULT_RULES, recursive=True)
    names = {p.name for files in grouped.values() for p in files}
    assert "loose.png" in names
    assert "deep.pdf" in names
    assert "already.jpg" not in names


def test_on_conflict_rename(tmp_path: Path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "notes.txt").write_text("existing", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("new", encoding="utf-8")

    organize(tmp_path, DEFAULT_RULES, _console(), on_conflict="rename", quiet=True)
    assert (docs / "notes.txt").read_text(encoding="utf-8") == "existing"
    assert (docs / "notes (1).txt").read_text(encoding="utf-8") == "new"


def test_on_conflict_skip(tmp_path: Path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "notes.txt").write_text("existing", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("new", encoding="utf-8")

    organize(tmp_path, DEFAULT_RULES, _console(), on_conflict="skip", quiet=True)
    assert (docs / "notes.txt").read_text(encoding="utf-8") == "existing"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "new"
    assert not (docs / "notes (1).txt").exists()


def test_report_json(tmp_path: Path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    report = tmp_path / "out.json"
    organize(
        tmp_path,
        DEFAULT_RULES,
        _console(),
        report_path=report,
        quiet=True,
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["moves"][0]["destination"].endswith("Code/a.py")


def test_skips_hidden_and_history(tmp_path: Path):
    (tmp_path / ".secret").write_text("x", encoding="utf-8")
    (tmp_path / HISTORY_FILENAME).write_text("{}", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("v", encoding="utf-8")
    grouped = scan_folder(tmp_path, DEFAULT_RULES)
    names = {p.name for files in grouped.values() for p in files}
    assert names == {"visible.txt"}


def test_cli_categories_and_version():
    from file_organiser.cli import main

    assert main(["categories"]) == 0
    # version exits via SystemExit
    try:
        main(["--version"])
    except SystemExit as e:
        assert e.code == 0
