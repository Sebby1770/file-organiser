"""Tests for duplicate detection."""
from __future__ import annotations

from pathlib import Path

from file_organiser.duplicates import choose_keeper, file_sha256, find_duplicates


def test_file_sha256_stable(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello world")
    b.write_bytes(b"hello world")
    assert file_sha256(a) == file_sha256(b)


def test_find_duplicates_groups_identical_content(tmp_path: Path):
    (tmp_path / "one.txt").write_text("same", encoding="utf-8")
    (tmp_path / "two.txt").write_text("same", encoding="utf-8")
    (tmp_path / "unique.txt").write_text("different", encoding="utf-8")

    groups = find_duplicates(tmp_path, recursive=False)
    assert len(groups) == 1
    paths = next(iter(groups.values()))
    names = {p.name for p in paths}
    assert names == {"one.txt", "two.txt"}


def test_no_duplicates(tmp_path: Path):
    (tmp_path / "a.txt").write_text("aaa", encoding="utf-8")
    (tmp_path / "b.txt").write_text("bbb", encoding="utf-8")
    assert find_duplicates(tmp_path) == {}


def test_choose_keeper_oldest_newest(tmp_path: Path):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("x", encoding="utf-8")
    new.write_text("x", encoding="utf-8")
    # Force mtimes
    import os
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    assert choose_keeper([old, new], keep="oldest") == old
    assert choose_keeper([old, new], keep="newest") == new


def test_delete_dupes_dry_run(tmp_path: Path, capsys):
    from rich.console import Console

    from file_organiser.duplicates import find_and_report_duplicates

    (tmp_path / "a.txt").write_text("dup", encoding="utf-8")
    (tmp_path / "b.txt").write_text("dup", encoding="utf-8")

    console = Console(force_terminal=False)
    find_and_report_duplicates(
        tmp_path,
        console,
        recursive=False,
        delete_dupes=True,
        keep="oldest",
        dry_run=True,
    )
    # Both still present after dry-run
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").exists()
