"""Tests for v1.3 features: clean, rename, diff, init-config, reclaimable, NO_COLOR."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from rich.console import Console

from file_organiser.clean import clean_folder, find_junk, is_protected_path
from file_organiser.console_util import env_no_color, make_console
from file_organiser.diff import compare_folders, diff_folders
from file_organiser.duplicates import find_and_report_duplicates, reclaimable_bytes
from file_organiser.history import HISTORY_FILENAME, HistoryManager
from file_organiser.rename import apply_pattern, plan_renames, rename_files, slugify
from file_organiser.rules import DEFAULT_RULES, init_config, local_config_path
from file_organiser.scanner import HASH_CACHE_FILENAME


def _console() -> Console:
    return Console(force_terminal=False, quiet=True)


def test_clean_dry_run_does_not_delete(tmp_path: Path, capsys):
    (tmp_path / "empty.txt").write_bytes(b"")
    (tmp_path / ".DS_Store").write_bytes(b"ds")
    (tmp_path / "Thumbs.db").write_bytes(b"th")
    (tmp_path / "keep.txt").write_text("data", encoding="utf-8")
    (tmp_path / "notes.tmp").write_bytes(b"tmp")
    (tmp_path / "backup~").write_bytes(b"bak")

    console = Console(force_terminal=False)
    n = clean_folder(tmp_path, console, apply=False, recursive=False)
    out = capsys.readouterr().out

    assert n >= 4
    assert "dry run" in out.lower() or "would remove" in out.lower()
    # Nothing deleted
    assert (tmp_path / "empty.txt").exists()
    assert (tmp_path / ".DS_Store").exists()
    assert (tmp_path / "Thumbs.db").exists()
    assert (tmp_path / "notes.tmp").exists()
    assert (tmp_path / "keep.txt").exists()


def test_clean_apply_removes_junk_not_keep(tmp_path: Path):
    (tmp_path / "empty.txt").write_bytes(b"")
    (tmp_path / "desktop.ini").write_bytes(b"ini")
    (tmp_path / "keep.pdf").write_bytes(b"pdf-data")
    # Protected internal files must survive
    (tmp_path / HISTORY_FILENAME).write_text("{}", encoding="utf-8")
    (tmp_path / HASH_CACHE_FILENAME).write_text("{}", encoding="utf-8")

    n = clean_folder(tmp_path, _console(), apply=True, recursive=False, quiet=True)
    assert n >= 2
    assert not (tmp_path / "empty.txt").exists()
    assert not (tmp_path / "desktop.ini").exists()
    assert (tmp_path / "keep.pdf").exists()
    assert (tmp_path / HISTORY_FILENAME).exists()
    assert (tmp_path / HASH_CACHE_FILENAME).exists()


def test_clean_never_touches_history_cache(tmp_path: Path):
    hist = tmp_path / HISTORY_FILENAME
    cache = tmp_path / HASH_CACHE_FILENAME
    hist.write_text("{}", encoding="utf-8")
    cache.write_text("{}", encoding="utf-8")
    # Even if empty (0 bytes) — still protected
    hist.write_bytes(b"")
    assert is_protected_path(hist)
    assert is_protected_path(cache)

    junk = find_junk(tmp_path, empty_files=True)
    names = {p.name for p, _, _ in junk}
    assert HISTORY_FILENAME not in names
    assert HASH_CACHE_FILENAME not in names


def test_rename_dry_run(tmp_path: Path, capsys):
    (tmp_path / "photo.jpg").write_bytes(b"img")
    (tmp_path / "snap.jpg").write_bytes(b"img2")

    console = Console(force_terminal=False)
    n = rename_files(
        tmp_path,
        console,
        pattern="IMG_{n:04d}{ext}",
        match="*.jpg",
        apply=False,
    )
    out = capsys.readouterr().out
    assert n == 2
    assert "dry run" in out.lower() or "would rename" in out.lower()
    # Originals untouched
    assert (tmp_path / "photo.jpg").exists()
    assert (tmp_path / "snap.jpg").exists()
    assert not (tmp_path / "IMG_0001.jpg").exists()


def test_rename_apply_with_pattern(tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"1")
    (tmp_path / "b.jpg").write_bytes(b"2")

    n = rename_files(
        tmp_path,
        _console(),
        pattern="IMG_{n:04d}{ext}",
        match="*.jpg",
        apply=True,
        quiet=True,
    )
    assert n == 2
    assert (tmp_path / "IMG_0001.jpg").exists()
    assert (tmp_path / "IMG_0002.jpg").exists()
    assert not (tmp_path / "a.jpg").exists()
    # History recorded for undo
    stack = HistoryManager(tmp_path).load_stack()
    assert len(stack) == 1
    assert stack[0]["mode"] == "rename"


def test_rename_slug(tmp_path: Path):
    (tmp_path / "My Cool Photo.JPG").write_bytes(b"x")
    pairs = plan_renames(tmp_path, slug=True)
    assert len(pairs) == 1
    assert pairs[0][1].name == "my-cool-photo.jpg"

    rename_files(tmp_path, _console(), slug=True, apply=True, quiet=True)
    assert (tmp_path / "my-cool-photo.jpg").exists()


def test_apply_pattern_tokens():
    p = Path("/tmp/photo.JPG")
    assert apply_pattern("IMG_{n:04d}{ext}", p, 7) == "IMG_0007.JPG"
    assert apply_pattern("{stem}_{n}{ext}", p, 3) == "photo_3.JPG"
    assert apply_pattern("{n}.{ext_no_dot}", p, 1) == "1.JPG"
    assert apply_pattern("{name}", p, 1) == "photo.JPG"


def test_slugify():
    assert slugify("Hello World.txt") == "hello-world.txt"
    assert slugify("  Foo__Bar  ") == "foo-bar" or "foo" in slugify("  Foo__Bar  ")


def test_diff_folders(tmp_path: Path, capsys):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    (a / "same.txt").write_text("identical", encoding="utf-8")
    (b / "same.txt").write_text("identical", encoding="utf-8")
    (a / "only_a.txt").write_text("a-only", encoding="utf-8")
    (b / "only_b.txt").write_text("b-only", encoding="utf-8")
    (a / "changed.txt").write_text("version-a", encoding="utf-8")
    (b / "changed.txt").write_text("version-b", encoding="utf-8")

    result = compare_folders(a, b, recursive=False)
    assert "only_a.txt" in result["only_a"]
    assert "only_b.txt" in result["only_b"]
    assert "same.txt" in result["identical"]
    diffs = [d[0] for d in result["different"]]
    assert "changed.txt" in diffs

    console = Console(force_terminal=False)
    counts = diff_folders(a, b, console, recursive=False)
    out = capsys.readouterr().out
    assert counts["only_a"] == 1
    assert counts["only_b"] == 1
    assert counts["different"] == 1
    assert counts["identical"] == 1
    assert "Summary" in out or "only" in out.lower()


def test_init_config_xdg(tmp_path: Path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    path = init_config(local=False, force=False)
    assert path.exists()
    assert path.name == "rules.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "Images" in data
    assert isinstance(data["Images"], list)

    # Second call without force fails
    try:
        init_config(local=False, force=False)
        raised = False
    except FileExistsError:
        raised = True
    assert raised

    # Force overwrites
    path2 = init_config(local=False, force=True)
    assert path2 == path


def test_init_config_local(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = init_config(local=True, force=False, cwd=tmp_path)
    assert path == local_config_path(tmp_path)
    assert path.name == ".file-organiser.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data.keys()) == set(DEFAULT_RULES.keys())


def test_cli_init_config(tmp_path: Path, monkeypatch, capsys):
    from file_organiser.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["init-config", "--local"]) == 0
    assert (tmp_path / ".file-organiser.json").exists()
    out = capsys.readouterr().out
    assert "Wrote config" in out or "config" in out.lower()


def test_reclaimable_space(tmp_path: Path, capsys):
    # Two groups of duplicates
    (tmp_path / "a1.txt").write_bytes(b"x" * 100)
    (tmp_path / "a2.txt").write_bytes(b"x" * 100)
    (tmp_path / "b1.bin").write_bytes(b"y" * 50)
    (tmp_path / "b2.bin").write_bytes(b"y" * 50)
    (tmp_path / "b3.bin").write_bytes(b"y" * 50)
    (tmp_path / "unique.txt").write_bytes(b"z" * 10)

    from file_organiser.duplicates import find_duplicates

    groups = find_duplicates(tmp_path, recursive=False, workers=1, use_cache=False)
    reclaim, count = reclaimable_bytes(groups, keep="oldest")
    # a: 1 extra * 100; b: 2 extras * 50 = 100; total 200
    assert count == 3
    assert reclaim == 200

    console = Console(force_terminal=False)
    find_and_report_duplicates(
        tmp_path,
        console,
        recursive=False,
        delete_dupes=False,
        use_cache=False,
    )
    out = capsys.readouterr().out
    assert "Reclaimable" in out or "reclaimable" in out.lower()
    assert "300" in out or "bytes" in out.lower()


def test_no_color_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert env_no_color() is False

    monkeypatch.setenv("NO_COLOR", "1")
    assert env_no_color() is True

    c = make_console()
    assert c.no_color is True


def test_cli_clean_rename_diff(tmp_path: Path, capsys):
    from file_organiser.cli import main

    (tmp_path / "empty.txt").write_bytes(b"")
    (tmp_path / "photo.jpg").write_bytes(b"img")

    assert main(["clean", str(tmp_path), "--no-recursive"]) == 0
    assert main(
        ["rename", str(tmp_path), "--pattern", "F_{n:02d}{ext}", "--match", "*.jpg"]
    ) == 0

    a = tmp_path / "left"
    b = tmp_path / "right"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("same", encoding="utf-8")
    (b / "f.txt").write_text("same", encoding="utf-8")
    assert main(["diff", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "identical" in out.lower() or "Summary" in out


def test_cli_bench(tmp_path: Path):
    from file_organiser.cli import main

    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    assert main(["bench", str(tmp_path), "--limit", "5"]) == 0


def test_version_is_130():
    from file_organiser import __version__

    assert __version__ == "1.3.0"
