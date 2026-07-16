"""Tests for v1.1 features: mime scan, stats, max-depth, prune, multi-undo, md report."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from rich.console import Console

from file_organiser.history import HISTORY_FILENAME, HistoryManager
from file_organiser.organizer import organize, prune_empty_dirs, show_stats, undo
from file_organiser.report import write_report
from file_organiser.rules import DEFAULT_RULES
from file_organiser.scanner import format_size, iter_files, scan_folder


def _console() -> Console:
    return Console(force_terminal=False, quiet=True)


def test_format_size():
    assert format_size(0) == "0B"
    assert format_size(512) == "512B"
    assert format_size(1024) == "1.0K"
    assert format_size(1024 * 1024) == "1.0M"


def test_max_depth_limits_scan(tmp_path: Path):
    (tmp_path / "top.txt").write_text("t", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "mid.txt").write_text("m", encoding="utf-8")
    deep = sub / "deep"
    deep.mkdir()
    (deep / "bottom.txt").write_text("b", encoding="utf-8")

    # max_depth 0 → top only (same as non-recursive)
    files0 = iter_files(tmp_path, recursive=True, max_depth=0, skip_category_folders=False)
    names0 = {p.name for p in files0}
    assert names0 == {"top.txt"}

    # max_depth 1 → top + sub, not deep
    files1 = iter_files(tmp_path, recursive=True, max_depth=1, skip_category_folders=False)
    names1 = {p.name for p in files1}
    assert "top.txt" in names1
    assert "mid.txt" in names1
    assert "bottom.txt" not in names1

    # unlimited recursive
    files_all = iter_files(tmp_path, recursive=True, skip_category_folders=False)
    assert {p.name for p in files_all} == {"top.txt", "mid.txt", "bottom.txt"}


def test_scan_folder_mime_fallback(tmp_path: Path):
    weird = tmp_path / "data.notarealext"
    weird.write_bytes(b"x")
    with mock.patch(
        "file_organiser.rules.mimetypes.guess_type",
        return_value=("image/png", None),
    ):
        grouped = scan_folder(tmp_path, DEFAULT_RULES, use_mime=True)
    assert "Images" in grouped
    assert grouped["Images"][0].name == "data.notarealext"

    grouped2 = scan_folder(tmp_path, DEFAULT_RULES, use_mime=False)
    assert "Other" in grouped2


def test_stats_command(tmp_path: Path, capsys):
    (tmp_path / "a.jpg").write_bytes(b"x" * 100)
    (tmp_path / "b.pdf").write_bytes(b"y" * 200)
    (tmp_path / "c.mp3").write_bytes(b"z" * 50)

    console = Console(force_terminal=False)
    show_stats(tmp_path, DEFAULT_RULES, console, recursive=False, top_n=5)
    out = capsys.readouterr().out
    assert "Stats" in out or "3" in out or "Files" in out


def test_prune_empty_dirs(tmp_path: Path):
    empty = tmp_path / "empty_one"
    empty.mkdir()
    nested = tmp_path / "parent" / "child"
    nested.mkdir(parents=True)
    # Non-empty should stay
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "file.txt").write_text("x", encoding="utf-8")

    n = prune_empty_dirs(tmp_path, _console(), quiet=True)
    assert n >= 2
    assert not empty.exists()
    assert not nested.exists()
    assert keep.exists()
    assert (keep / "file.txt").exists()
    # Root never deleted
    assert tmp_path.exists()


def test_prune_dry_run_no_delete(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    n = prune_empty_dirs(tmp_path, _console(), dry_run=True, quiet=True)
    assert n >= 1
    assert empty.exists()


def test_organize_prune_empty(tmp_path: Path):
    nested = tmp_path / "inbox"
    nested.mkdir()
    (nested / "photo.png").write_bytes(b"\x89PNG")

    organize(
        tmp_path,
        DEFAULT_RULES,
        _console(),
        recursive=True,
        prune_empty=True,
        quiet=True,
    )
    assert (tmp_path / "Images" / "photo.png").exists()
    # inbox should be pruned as empty after move
    assert not nested.exists()


def test_multi_undo_stack(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    organize(tmp_path, DEFAULT_RULES, _console(), quiet=True)
    assert (tmp_path / "Documents" / "a.txt").exists()

    (tmp_path / "b.png").write_bytes(b"png")
    organize(tmp_path, DEFAULT_RULES, _console(), quiet=True)
    assert (tmp_path / "Images" / "b.png").exists()

    hm = HistoryManager(tmp_path)
    assert len(hm.load_stack()) == 2

    # First undo restores b.png
    undo(tmp_path, _console(), quiet=True)
    assert (tmp_path / "b.png").exists()
    assert not (tmp_path / "Images" / "b.png").exists()
    assert (tmp_path / "Documents" / "a.txt").exists()
    assert len(HistoryManager(tmp_path).load_stack()) == 1

    # Second undo restores a.txt
    undo(tmp_path, _console(), quiet=True)
    assert (tmp_path / "a.txt").exists()
    assert not (tmp_path / HISTORY_FILENAME).exists()


def test_undo_list(tmp_path: Path, capsys):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    organize(tmp_path, DEFAULT_RULES, _console(), quiet=True)
    console = Console(force_terminal=False)
    undo(tmp_path, console, list_only=True)
    out = capsys.readouterr().out
    assert "history" in out.lower() or "snapshot" in out.lower() or "move" in out.lower()
    # Still has history after list
    assert (tmp_path / HISTORY_FILENAME).exists()


def test_markdown_report(tmp_path: Path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    report = tmp_path / "out.md"
    organize(
        tmp_path,
        DEFAULT_RULES,
        _console(),
        report_path=report,
        quiet=True,
    )
    text = report.read_text(encoding="utf-8")
    assert text.startswith("# File organiser report")
    assert "Source" in text
    assert "a.py" in text


def test_write_report_md_direct(tmp_path: Path):
    src = tmp_path / "s.txt"
    dst = tmp_path / "Documents" / "s.txt"
    out = tmp_path / "r.markdown"
    write_report(out, [(dst, src)], mode="move", dry_run=False)
    assert "Documents" in out.read_text(encoding="utf-8")


def test_parallel_duplicates_same_result(tmp_path: Path):
    from file_organiser.duplicates import find_duplicates

    (tmp_path / "one.txt").write_text("same", encoding="utf-8")
    (tmp_path / "two.txt").write_text("same", encoding="utf-8")
    (tmp_path / "three.txt").write_text("other", encoding="utf-8")

    g1 = find_duplicates(tmp_path, recursive=False, workers=1)
    g2 = find_duplicates(tmp_path, recursive=False, workers=4)
    assert len(g1) == 1
    assert len(g2) == 1
    names1 = {p.name for p in next(iter(g1.values()))}
    names2 = {p.name for p in next(iter(g2.values()))}
    assert names1 == names2 == {"one.txt", "two.txt"}


def test_cli_stats_and_prune(tmp_path: Path):
    from file_organiser.cli import main

    (tmp_path / "f.txt").write_text("hi", encoding="utf-8")
    empty = tmp_path / "e"
    empty.mkdir()
    assert main(["stats", str(tmp_path), "--no-recursive"]) == 0
    assert main(["prune", str(tmp_path), "--dry-run"]) == 0
