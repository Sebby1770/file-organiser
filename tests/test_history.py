"""Tests for HistoryManager."""
from __future__ import annotations

from pathlib import Path

from file_organiser.history import HISTORY_FILENAME, HistoryManager


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
