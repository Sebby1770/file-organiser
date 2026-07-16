"""Tests for rules loading and category lookup."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from file_organiser.rules import (
    DEFAULT_RULES,
    OTHER_CATEGORY,
    category_for_extension,
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
