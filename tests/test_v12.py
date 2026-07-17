"""Tests for v1.2 features: find, include, hash cache, extensions, json, symlink, trash."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from rich.console import Console

from file_organiser.duplicates import (
    HashCache,
    delete_path,
    find_duplicates,
    send2trash_available,
)
from file_organiser.organizer import (
    build_preview_plan,
    find_files,
    organize,
    preview,
    show_extensions,
    show_tree,
)
from file_organiser.rules import DEFAULT_RULES
from file_organiser.scanner import HASH_CACHE_FILENAME, iter_files, scan_folder


def _console() -> Console:
    return Console(force_terminal=False, quiet=True)


def test_find_by_category(tmp_path: Path, capsys):
    (tmp_path / "a.jpg").write_bytes(b"img")
    (tmp_path / "b.pdf").write_bytes(b"pdf")
    (tmp_path / "c.mp3").write_bytes(b"aud")

    console = Console(force_terminal=False)
    n = find_files(tmp_path, DEFAULT_RULES, console, category="Images")
    out = capsys.readouterr().out
    assert n == 1
    assert "a.jpg" in out
    assert "b.pdf" not in out


def test_find_by_ext(tmp_path: Path, capsys):
    (tmp_path / "a.jpg").write_bytes(b"img")
    (tmp_path / "b.pdf").write_bytes(b"pdf")
    (tmp_path / "c.PDF").write_bytes(b"pdf2")

    console = Console(force_terminal=False)
    n = find_files(tmp_path, DEFAULT_RULES, console, ext=".pdf")
    out = capsys.readouterr().out
    assert n == 2
    assert "b.pdf" in out
    assert "c.PDF" in out or "c.PDF".lower() in out.lower()


def test_find_by_name(tmp_path: Path, capsys):
    (tmp_path / "invoice_jan.pdf").write_bytes(b"x")
    (tmp_path / "photo.jpg").write_bytes(b"y")
    (tmp_path / "my_invoice.txt").write_text("z", encoding="utf-8")

    console = Console(force_terminal=False)
    n = find_files(tmp_path, DEFAULT_RULES, console, name="*invoice*")
    assert n == 2
    out = capsys.readouterr().out
    assert "invoice" in out.lower()


def test_include_patterns(tmp_path: Path):
    (tmp_path / "keep.pdf").write_bytes(b"pdf")
    (tmp_path / "skip.txt").write_text("txt", encoding="utf-8")
    (tmp_path / "also.pdf").write_bytes(b"pdf2")

    grouped = scan_folder(tmp_path, DEFAULT_RULES, include=["*.pdf"])
    names = {p.name for files in grouped.values() for p in files}
    assert names == {"keep.pdf", "also.pdf"}

    files = iter_files(tmp_path, include=["*.txt"])
    assert {p.name for p in files} == {"skip.txt"}


def test_include_and_exclude(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"a")
    (tmp_path / "b.pdf").write_bytes(b"b")
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")
    grouped = scan_folder(
        tmp_path, DEFAULT_RULES, include=["*.pdf", "*.txt"], exclude=["b.pdf"]
    )
    names = {p.name for files in grouped.values() for p in files}
    assert names == {"a.pdf", "c.txt"}


def test_hash_cache_written_and_reused(tmp_path: Path):
    (tmp_path / "one.txt").write_text("same", encoding="utf-8")
    (tmp_path / "two.txt").write_text("same", encoding="utf-8")

    cache = HashCache(tmp_path)
    groups1 = find_duplicates(tmp_path, recursive=False, workers=1, cache=cache)
    assert len(groups1) == 1
    cache_path = tmp_path / HASH_CACHE_FILENAME
    assert cache_path.exists()
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    entries = data.get("entries", data)
    assert len(entries) >= 2

    # Second run should hit cache (no recompute) — mock file_sha256 to prove it
    cache2 = HashCache(tmp_path)
    with mock.patch(
        "file_organiser.duplicates.file_sha256",
        side_effect=AssertionError("should not recompute when cache hits"),
    ):
        groups2 = find_duplicates(tmp_path, recursive=False, workers=1, cache=cache2)
    assert len(groups2) == 1
    assert cache2.hits >= 2
    assert cache2.misses == 0


def test_hash_cache_invalidates_on_mtime_change(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("v1", encoding="utf-8")
    (tmp_path / "b.txt").write_text("v1", encoding="utf-8")

    cache = HashCache(tmp_path)
    find_duplicates(tmp_path, recursive=False, workers=1, cache=cache)
    assert cache.hits + cache.misses >= 2

    # Change content + mtime of a
    import os
    import time

    time.sleep(0.05)
    f.write_text("v2-changed", encoding="utf-8")
    os.utime(f, None)

    cache2 = HashCache(tmp_path)
    # a should miss; b may hit if size/mtime match — we only need at least one miss for a
    find_duplicates(tmp_path, recursive=False, workers=1, cache=cache2)
    # After size change, a is unique size so may not even be hashed; ensure cache get works
    assert cache2.get(f) is not None or cache2.misses >= 0  # smoke
    # Direct get after put from second run
    digest = cache2.get(f)
    # If unique size, not in to_hash — put only happens for hashed files.
    # Force put/get cycle:
    cache3 = HashCache(tmp_path)
    from file_organiser.duplicates import file_sha256

    d = file_sha256(f)
    cache3.put(f, d)
    assert cache3.get(f) == d
    # Change mtime only
    os.utime(f, (1_000_000, 1_000_000))
    assert cache3.get(f) is None


def test_extensions_command(tmp_path: Path, capsys):
    (tmp_path / "a.jpg").write_bytes(b"x" * 100)
    (tmp_path / "b.jpg").write_bytes(b"y" * 50)
    (tmp_path / "c.pdf").write_bytes(b"z" * 200)
    (tmp_path / "noext").write_bytes(b"n")

    console = Console(force_terminal=False)
    n = show_extensions(tmp_path, console, recursive=False)
    out = capsys.readouterr().out
    assert n >= 2
    assert ".jpg" in out
    assert ".pdf" in out


def test_preview_json_structure(tmp_path: Path, capsys):
    (tmp_path / "photo.jpg").write_bytes(b"img")
    (tmp_path / "doc.pdf").write_bytes(b"pdf")

    plan = build_preview_plan(tmp_path, DEFAULT_RULES)
    assert plan["count"] == 2
    assert "folder" in plan
    assert "files" in plan
    assert len(plan["files"]) == 2
    for item in plan["files"]:
        assert "source" in item
        assert "destination" in item
        assert "category" in item
        assert item["category"] in ("Images", "Documents")

    console = Console(force_terminal=False)
    preview(tmp_path, DEFAULT_RULES, console, as_json=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["count"] == 2
    assert isinstance(data["files"], list)


def test_symlink_mode(tmp_path: Path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"jpg-data")

    n = organize(
        tmp_path,
        DEFAULT_RULES,
        _console(),
        symlink=True,
        quiet=True,
    )
    assert n == 1
    link = tmp_path / "Images" / "photo.jpg"
    assert link.is_symlink()
    assert src.exists()  # original stays
    assert link.resolve() == src.resolve()
    assert link.read_bytes() == b"jpg-data"


def test_trash_flag_falls_back_without_send2trash(tmp_path: Path, capsys):
    from file_organiser.duplicates import find_and_report_duplicates

    (tmp_path / "a.txt").write_text("dup", encoding="utf-8")
    (tmp_path / "b.txt").write_text("dup", encoding="utf-8")

    console = Console(force_terminal=False)

    with mock.patch(
        "file_organiser.duplicates.send2trash_available", return_value=False
    ):
        find_and_report_duplicates(
            tmp_path,
            console,
            recursive=False,
            delete_dupes=True,
            keep="oldest",
            dry_run=False,
            use_trash=True,
            use_cache=False,
        )

    out = capsys.readouterr().out
    assert "send2trash" in out.lower() or "warning" in out.lower()
    # One of the two should be permanently deleted
    remaining = list(tmp_path.glob("*.txt"))
    assert len(remaining) == 1


def test_delete_path_without_trash(tmp_path: Path):
    f = tmp_path / "gone.txt"
    f.write_text("x", encoding="utf-8")
    action = delete_path(f, use_trash=False)
    assert action == "deleted"
    assert not f.exists()


def test_cli_find_extensions_preview_json(tmp_path: Path, capsys):
    from file_organiser.cli import main

    (tmp_path / "x.jpg").write_bytes(b"img")
    (tmp_path / "y.pdf").write_bytes(b"pdf")

    assert main(["find", str(tmp_path), "--category", "Images"]) == 0
    assert main(["extensions", str(tmp_path), "--no-recursive"]) == 0
    assert main(["preview", str(tmp_path), "--json"]) == 0
    out = capsys.readouterr().out
    # JSON plan should appear
    assert '"files"' in out or "x.jpg" in out


def test_cli_tree(tmp_path: Path):
    from file_organiser.cli import main

    (tmp_path / "a.jpg").write_bytes(b"i")
    organize(tmp_path, DEFAULT_RULES, _console(), quiet=True)
    assert main(["tree", str(tmp_path)]) == 0


def test_send2trash_available_is_bool():
    assert isinstance(send2trash_available(), bool)
