"""Tests for HistoryManager multi-level undo stack."""
from __future__ import annotations

import json
from pathlib import Path

from file_organiser.history import HISTORY_FILENAME, MAX_HISTORY_STACK, HistoryManager


def test_save_load_clear(tmp_path: Path):
    hm = HistoryManager(tmp_path)
    src = tmp_path / "a.txt"
    dst = tmp_path / "Documents" / "a.txt"
    # Stored as (current, original)
    hm.save([(dst, src)], mode="move")

    assert (tmp_path / HISTORY_FILENAME).exists()
    moves = hm.load()
    assert len(moves) == 1
    assert moves[0][0] == dst
    assert moves[0][1] == src
    assert hm.load_mode() == "move"

    hm.clear()
    assert not (tmp_path / HISTORY_FILENAME).exists()
    assert hm.load() == []


def test_copy_mode_persisted(tmp_path: Path):
    hm = HistoryManager(tmp_path)
    hm.save([(tmp_path / "x", tmp_path / "y")], mode="copy")
    assert hm.load_mode() == "copy"


def test_load_missing_returns_empty(tmp_path: Path):
    hm = HistoryManager(tmp_path)
    assert hm.load() == []
    assert hm.load_mode() == "move"


def test_stack_push_and_pop(tmp_path: Path):
    hm = HistoryManager(tmp_path)
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"
    d = tmp_path / "d"

    hm.save([(b, a)], mode="move")
    hm.save([(d, c)], mode="copy")

    stack = hm.load_stack()
    assert len(stack) == 2
    assert stack[-1]["mode"] == "copy"
    assert hm.load_mode() == "copy"

    snap = hm.pop()
    assert snap is not None
    assert snap["mode"] == "copy"
    assert len(hm.load_stack()) == 1
    assert hm.load_mode() == "move"

    snap2 = hm.pop()
    assert snap2 is not None
    assert snap2["mode"] == "move"
    assert hm.load_stack() == []
    assert not (tmp_path / HISTORY_FILENAME).exists()


def test_legacy_single_dict_compatible(tmp_path: Path):
    """Old format: a single snapshot dict without 'stack' key."""
    legacy = {
        "timestamp": "2024-01-01T12:00:00",
        "mode": "move",
        "moves": [["/tmp/dest", "/tmp/src"]],
    }
    path = tmp_path / HISTORY_FILENAME
    path.write_text(json.dumps(legacy), encoding="utf-8")

    hm = HistoryManager(tmp_path)
    assert len(hm.load_stack()) == 1
    assert hm.load_mode() == "move"
    moves = hm.load()
    assert moves[0][0] == Path("/tmp/dest")
    assert moves[0][1] == Path("/tmp/src")

    # After pop, file gone
    hm.pop()
    assert hm.load_stack() == []


def test_list_snapshots_newest_first(tmp_path: Path):
    hm = HistoryManager(tmp_path)
    hm.save([(tmp_path / "1", tmp_path / "a")], mode="move")
    hm.save([(tmp_path / "2", tmp_path / "b")], mode="copy")
    snaps = hm.list_snapshots()
    assert len(snaps) == 2
    assert snaps[0]["index"] == 0
    assert snaps[0]["mode"] == "copy"
    assert snaps[1]["mode"] == "move"


def test_stack_capped_at_max(tmp_path: Path):
    hm = HistoryManager(tmp_path)
    for i in range(MAX_HISTORY_STACK + 5):
        hm.save([(tmp_path / f"d{i}", tmp_path / f"s{i}")], mode="move")
    assert len(hm.load_stack()) == MAX_HISTORY_STACK
