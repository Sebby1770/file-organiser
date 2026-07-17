from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from file_organizer.errors import SafetyError
from file_organizer.planner import create_plan


class PlannerTests(unittest.TestCase):
    def test_planning_is_deterministic_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "zeta.jpg").write_bytes(b"image-z")
            (root / "alpha.txt").write_text("notes", encoding="utf-8")
            before = snapshot(root)

            first = create_plan(root)
            second = create_plan(root)

            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(snapshot(root), before)
            self.assertEqual(
                [(move.source.name, move.category) for move in first.moves],
                [("alpha.txt", "Documents"), ("zeta.jpg", "Images")],
            )

    def test_collision_rename_is_reserved_during_planning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "photo.png").write_bytes(b"new")
            (root / "Images").mkdir()
            (root / "Images" / "photo.png").write_bytes(b"existing")
            plan = create_plan(root, collision_strategy="rename")
            self.assertEqual(plan.moves[0].destination.name, "photo (1).png")

    def test_planned_casefold_collisions_are_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "A.txt").write_text("first", encoding="utf-8")
            (root / "a.TXT").write_text("second", encoding="utf-8")
            if len(list(root.iterdir())) < 2:
                self.skipTest("filesystem is case-insensitive")
            plan = create_plan(root)
            destinations = [move.destination.name for move in plan.moves]
            self.assertEqual(len({name.casefold() for name in destinations}), 2)
            self.assertTrue(any("(1)" in name for name in destinations))

    def test_recursive_scan_skips_symlinks_and_managed_directories(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as outside,
        ):
            root = Path(temporary)
            nested = root / "incoming"
            nested.mkdir()
            (nested / "report.pdf").write_bytes(b"pdf")
            managed = root / "Documents"
            managed.mkdir()
            (managed / "already.txt").write_text("stay", encoding="utf-8")
            link = root / "external-link"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is unavailable")

            plan = create_plan(root, recursive=True)

            self.assertEqual(len(plan.moves), 1)
            self.assertEqual(
                plan.moves[0].destination.relative_to(plan.root).as_posix(),
                "Documents/incoming/report.pdf",
            )
            reasons = {item.reason for item in plan.skipped}
            self.assertIn("managed-directory", reasons)
            self.assertIn("symlink", reasons)

    def test_duplicate_skip_keeps_canonical_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.txt").write_text("same", encoding="utf-8")
            (root / "b.txt").write_text("same", encoding="utf-8")
            plan = create_plan(root, duplicate_strategy="skip")
            self.assertEqual([move.source.name for move in plan.moves], ["a.txt"])
            duplicate_skips = [
                item for item in plan.skipped if item.reason == "duplicate"
            ]
            self.assertEqual([item.path.name for item in duplicate_skips], ["b.txt"])

    def test_dangerous_filesystem_root_is_refused(self) -> None:
        with self.assertRaises(SafetyError):
            create_plan(Path(os.path.abspath(os.sep)))


def snapshot(root: Path) -> list[tuple[str, str, bytes | None]]:
    result: list[tuple[str, str, bytes | None]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result.append((relative, "symlink", None))
        elif path.is_dir():
            result.append((relative, "directory", None))
        else:
            result.append((relative, "file", path.read_bytes()))
    return result


if __name__ == "__main__":
    unittest.main()
