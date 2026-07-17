from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from file_organizer.errors import ConfigurationError
from file_organizer.rules import default_rules, load_rules


class RuleTests(unittest.TestCase):
    def write_config(self, directory: Path, payload: object) -> Path:
        path = directory / "rules.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_longest_extension_wins(self) -> None:
        rules = load_rules_from_payload(
            {"categories": {"Archives": [".gz", ".tar.gz"], "OtherCode": [".py"]}}
        )
        self.assertEqual(rules.category_for("backup.tar.gz"), "Archives")
        self.assertEqual(rules.category_for("SCRIPT.PY"), "OtherCode")

    def test_legacy_shape_is_supported(self) -> None:
        rules = load_rules_from_payload({"Pictures": ["jpg", ".png"]})
        self.assertEqual(rules.category_for("photo.JPG"), "Pictures")
        self.assertEqual(rules.category_for("notes.txt"), "Other")

    def test_rejects_unsafe_cross_platform_category_names(self) -> None:
        for category in [
            "..",
            "../escape",
            r"..\escape",
            r"C:\escape",
            "NUL",
            "name.",
            ".file-organizer",
        ]:
            with self.subTest(category=category):
                with self.assertRaises(ConfigurationError):
                    load_rules_from_payload({category: [".txt"]})

    def test_rejects_casefold_category_collisions(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_rules_from_payload({"Images": [".jpg"], "images": [".png"]})

    def test_rejects_ambiguous_extensions_and_default_category(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_rules_from_payload({"A": [".txt"], "B": ["TXT"]})
        with self.assertRaises(ConfigurationError):
            load_rules_from_payload(
                {"categories": {"Other": [".txt"]}, "default_category": "other"}
            )

    def test_defaults_cover_common_extensions(self) -> None:
        rules = default_rules()
        self.assertEqual(rules.category_for("photo.webp"), "Images")
        self.assertEqual(rules.category_for("archive.tar.gz"), "Archives")
        self.assertEqual(rules.category_for("unknown.filetype"), "Other")


def load_rules_from_payload(payload: object):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "rules.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_rules(path)


if __name__ == "__main__":
    unittest.main()
