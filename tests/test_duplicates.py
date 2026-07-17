from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from file_organizer.duplicates import find_duplicates


class DuplicateTests(unittest.TestCase):
    def test_groups_by_full_content_hash_and_includes_empty_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.bin").write_bytes(b"same content")
            (root / "b.bin").write_bytes(b"same content")
            (root / "same-size.bin").write_bytes(b"other bytes!")
            (root / "empty-a").write_bytes(b"")
            (root / "empty-b").write_bytes(b"")

            report = find_duplicates(root)

            names = [{path.name for path in group.files} for group in report.groups]
            self.assertIn({"a.bin", "b.bin"}, names)
            self.assertIn({"empty-a", "empty-b"}, names)
            self.assertNotIn("same-size.bin", set().union(*names))
            self.assertEqual(report.to_dict()["summary"]["duplicate_groups"], 2)

    def test_symlinks_are_reported_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.txt"
            original.write_text("content", encoding="utf-8")
            link = root / "link.txt"
            try:
                link.symlink_to(original)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            report = find_duplicates(root)
            self.assertFalse(report.groups)
            self.assertIn("symlink", {item.reason for item in report.skipped})


if __name__ == "__main__":
    unittest.main()
