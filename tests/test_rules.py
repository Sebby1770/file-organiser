"""Tests for rules loading, category lookup, MIME fallback, config discovery."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from file_organiser.rules import (
    DEFAULT_RULES,
    OTHER_CATEGORY,
    category_for_extension,
    category_for_mime,
    category_for_path,
    discover_config,
    load_rules,
)


def test_default_rules_loaded():
    rules = load_rules(None)
    assert "Images" in rules
    assert ".jpg" in rules["Images"]
    assert ".pdf" in rules["Documents"]


def test_category_for_known_extension():
    assert category_for_extension(".png", DEFAULT_RULES) == "Images"
    assert category_for_extension(".PDF", DEFAULT_RULES) == "Documents"
    assert category_for_extension(".Py", DEFAULT_RULES) == "Code"


def test_category_for_unknown_is_other():
    assert category_for_extension(".xyzunknown", DEFAULT_RULES) == OTHER_CATEGORY
    assert category_for_extension("", DEFAULT_RULES) == OTHER_CATEGORY


def test_load_custom_config(tmp_path: Path):
    cfg = tmp_path / "rules.json"
    cfg.write_text(
        json.dumps({"Photos": ["jpg", ".PNG"], "Notes": [".txt"]}),
        encoding="utf-8",
    )
    rules = load_rules(cfg)
    assert rules["Photos"] == [".jpg", ".png"]
    assert rules["Notes"] == [".txt"]
    assert category_for_extension(".jpg", rules) == "Photos"
    assert category_for_extension(".md", rules) == OTHER_CATEGORY


def test_missing_config_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_rules(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path: Path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_rules(cfg)


def test_invalid_structure_raises(tmp_path: Path):
    cfg = tmp_path / "list.json"
    cfg.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_rules(cfg)


def test_category_for_mime_prefixes():
    assert category_for_mime("image/png") == "Images"
    assert category_for_mime("video/mp4") == "Videos"
    assert category_for_mime("audio/mpeg") == "Audio"
    assert category_for_mime("text/plain") == "Documents"
    assert category_for_mime("application/pdf") == "Documents"
    assert category_for_mime("application/zip") == "Archives"
    assert category_for_mime(None) == OTHER_CATEGORY
    assert category_for_mime("application/octet-stream") == OTHER_CATEGORY


def test_mime_fallback_when_extension_unknown(tmp_path: Path):
    # .xyz is not in default rules; with use_mime, mimetypes may still return None
    # Use a name that mimetypes knows via extensionless override mock
    weird = tmp_path / "photo.unknownext"
    weird.write_bytes(b"x")
    assert category_for_path(weird, DEFAULT_RULES, use_mime=False) == OTHER_CATEGORY

    with mock.patch(
        "file_organiser.rules.mimetypes.guess_type",
        return_value=("image/jpeg", None),
    ):
        assert category_for_path(weird, DEFAULT_RULES, use_mime=True) == "Images"

    with mock.patch(
        "file_organiser.rules.mimetypes.guess_type",
        return_value=("application/pdf", None),
    ):
        assert category_for_path(weird, DEFAULT_RULES, use_mime=True) == "Documents"


def test_mime_not_used_when_extension_known(tmp_path: Path):
    p = tmp_path / "notes.txt"
    p.write_text("hi", encoding="utf-8")
    with mock.patch(
        "file_organiser.rules.mimetypes.guess_type",
        return_value=("image/png", None),
    ) as guess:
        cat = category_for_path(p, DEFAULT_RULES, use_mime=True)
        assert cat == "Documents"
        guess.assert_not_called()


def test_discover_config_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / ".file-organiser.json"
    cfg.write_text(json.dumps({"Images": [".jpg"]}), encoding="utf-8")
    found = discover_config(None)
    assert found is not None
    assert found.resolve() == cfg.resolve()


def test_discover_config_explicit_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / ".file-organiser.json"
    local.write_text("{}", encoding="utf-8")
    other = tmp_path / "custom.json"
    other.write_text(json.dumps({"X": [".x"]}), encoding="utf-8")
    found = discover_config(other)
    assert found.resolve() == other.resolve()


def test_discover_config_none_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # Point home to empty temp so ~/.config is not used
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert discover_config(None) is None
