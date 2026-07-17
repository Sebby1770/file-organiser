from __future__ import annotations

import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import file_organizer.executor as executor
from file_organizer.errors import ConflictError, HistoryError, TransactionError
from file_organizer.planner import create_plan


class TransactionTests(unittest.TestCase):
    def test_apply_and_undo_keep_stacked_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            unrelated = root / "unrelated-empty-directory"
            unrelated.mkdir()
            (root / "first.txt").write_text("first", encoding="utf-8")

            first = executor.apply_plan(create_plan(root))
            (root / "second.jpg").write_bytes(b"second")
            second = executor.apply_plan(create_plan(root))

            history = executor.list_history(root)
            self.assertEqual(
                [item["operation_id"] for item in history],
                [second.operation_id, first.operation_id],
            )
            self.assertEqual(
                executor.create_undo_plan(root).operation_id, second.operation_id
            )
            executor.apply_undo(executor.create_undo_plan(root))
            self.assertEqual(
                executor.create_undo_plan(root).operation_id, first.operation_id
            )
            executor.apply_undo(executor.create_undo_plan(root))

            self.assertEqual((root / "first.txt").read_text(encoding="utf-8"), "first")
            self.assertEqual((root / "second.jpg").read_bytes(), b"second")
            self.assertTrue(unrelated.is_dir())
            self.assertEqual(
                [item["status"] for item in executor.list_history(root)],
                ["undone", "undone"],
            )

    def test_preflight_detects_changed_source_before_any_move(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = root / "first.txt"
            second = root / "second.jpg"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            plan = create_plan(root)
            second.write_text("replacement", encoding="utf-8")

            with self.assertRaises(ConflictError):
                executor.apply_plan(plan)

            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())
            self.assertFalse((root / "Documents").exists())
            self.assertFalse((root / "Images").exists())

    def test_preflight_detects_destination_created_after_planning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "note.txt"
            source.write_text("source", encoding="utf-8")
            plan = create_plan(root)
            destination = root / "Documents" / "note.txt"
            destination.parent.mkdir()
            destination.write_text("race winner", encoding="utf-8")

            with self.assertRaises(ConflictError):
                executor.apply_plan(plan)

            self.assertEqual(source.read_text(encoding="utf-8"), "source")
            self.assertEqual(destination.read_text(encoding="utf-8"), "race winner")

    def test_apply_failure_rolls_back_completed_moves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "first.txt").write_text("first", encoding="utf-8")
            (root / "second.jpg").write_text("second", encoding="utf-8")
            plan = create_plan(root)
            real_move = executor._move_no_overwrite
            calls = 0

            def fail_second_move(*args: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected move failure")
                real_move(*args)  # type: ignore[arg-type]

            with mock.patch.object(executor, "_move_no_overwrite", fail_second_move):
                with self.assertRaises(TransactionError) as raised:
                    executor.apply_plan(plan)

            self.assertTrue(raised.exception.rollback_succeeded)
            self.assertEqual(raised.exception.rollback_count, 1)
            self.assertEqual((root / "first.txt").read_text(encoding="utf-8"), "first")
            self.assertEqual(
                (root / "second.jpg").read_text(encoding="utf-8"), "second"
            )
            self.assertEqual(executor.list_history(root)[0]["status"], "rolled_back")

    def test_atomic_source_replacement_is_preserved_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "note.txt"
            source.write_text("planned bytes", encoding="utf-8")
            plan = create_plan(root)
            destination = root / "Documents" / "note.txt"
            real_fingerprint = executor.fingerprint_file
            replaced = False

            def replace_after_destination_hash(
                path: Path, hash_root: Path | None = None
            ) -> object:
                nonlocal replaced
                result = real_fingerprint(path, hash_root)
                if path == destination and not replaced:
                    replacement = root / "replacement.tmp"
                    replacement.write_text("concurrent replacement", encoding="utf-8")
                    os.replace(replacement, source)
                    replaced = True
                return result

            with mock.patch.object(
                executor, "fingerprint_file", replace_after_destination_hash
            ):
                with self.assertRaises(TransactionError) as raised:
                    executor.apply_plan(plan)

            self.assertFalse(raised.exception.rollback_succeeded)
            self.assertEqual(
                source.read_text(encoding="utf-8"), "concurrent replacement"
            )
            self.assertEqual(destination.read_text(encoding="utf-8"), "planned bytes")
            self.assertEqual(executor.list_history(root)[0]["status"], "apply_partial")
            self.assertEqual(list(root.rglob(".file-organizer-quarantine-*")), [])

    def test_cross_device_source_replacement_is_preserved_without_data_loss(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "note.txt"
            source.write_text("planned bytes", encoding="utf-8")
            plan = create_plan(root)
            destination = root / "Documents" / "note.txt"
            real_link = os.link
            real_fingerprint = executor.fingerprint_file
            replaced = False

            def force_planned_move_across_devices(
                source_path: object, destination_path: object, **kwargs: object
            ) -> None:
                if (
                    Path(source_path) == source
                    and Path(destination_path) == destination
                ):
                    raise OSError(errno.EXDEV, "injected cross-device move")
                real_link(source_path, destination_path, **kwargs)  # type: ignore[arg-type]

            def replace_after_destination_hash(
                path: Path, hash_root: Path | None = None
            ) -> object:
                nonlocal replaced
                result = real_fingerprint(path, hash_root)
                if path == destination and not replaced:
                    replacement = root / "replacement.tmp"
                    replacement.write_text("concurrent replacement", encoding="utf-8")
                    os.replace(replacement, source)
                    replaced = True
                return result

            with (
                mock.patch.object(
                    executor.os, "link", force_planned_move_across_devices
                ),
                mock.patch.object(
                    executor, "fingerprint_file", replace_after_destination_hash
                ),
            ):
                with self.assertRaises(TransactionError) as raised:
                    executor.apply_plan(plan)

            self.assertFalse(raised.exception.rollback_succeeded)
            self.assertEqual(
                source.read_text(encoding="utf-8"), "concurrent replacement"
            )
            self.assertEqual(destination.read_text(encoding="utf-8"), "planned bytes")
            self.assertEqual(executor.list_history(root)[0]["status"], "apply_partial")
            self.assertEqual(list(root.rglob(".file-organizer-quarantine-*")), [])

    def test_apply_interrupt_after_committed_move_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "note.txt"
            source.write_text("planned bytes", encoding="utf-8")
            destination = root / "Documents" / "note.txt"
            real_move = executor._move_no_overwrite

            def interrupt_after_move(*args: object) -> None:
                real_move(*args)  # type: ignore[arg-type]
                raise KeyboardInterrupt

            with mock.patch.object(
                executor, "_move_no_overwrite", interrupt_after_move
            ):
                with self.assertRaises(KeyboardInterrupt):
                    executor.apply_plan(create_plan(root))

            self.assertEqual(source.read_text(encoding="utf-8"), "planned bytes")
            self.assertFalse(destination.exists())
            self.assertEqual(executor.list_history(root)[0]["status"], "rolled_back")
            self.assertEqual(list(root.rglob(".file-organizer-quarantine-*")), [])

    def test_interrupt_raised_by_committing_unlink_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "note.txt"
            source.write_text("planned bytes", encoding="utf-8")
            destination = root / "Documents" / "note.txt"
            real_unlink = os.unlink
            interrupted = False

            def unlink_then_interrupt(
                path: object, *args: object, **kwargs: object
            ) -> None:
                nonlocal interrupted
                real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]
                if Path(path).name == "entry" and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt

            with mock.patch.object(executor.os, "unlink", unlink_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    executor.apply_plan(create_plan(root))

            self.assertTrue(interrupted)
            self.assertEqual(source.read_text(encoding="utf-8"), "planned bytes")
            self.assertFalse(destination.exists())
            self.assertEqual(executor.list_history(root)[0]["status"], "rolled_back")
            self.assertEqual(list(root.rglob(".file-organizer-quarantine-*")), [])

    def test_undo_interrupt_after_committed_move_restores_applied_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "note.txt"
            source.write_text("planned bytes", encoding="utf-8")
            result = executor.apply_plan(create_plan(root))
            destination = root / "Documents" / "note.txt"
            undo = executor.create_undo_plan(root, result.operation_id)
            real_move = executor._move_no_overwrite

            def interrupt_after_move(*args: object) -> None:
                real_move(*args)  # type: ignore[arg-type]
                raise KeyboardInterrupt

            with mock.patch.object(
                executor, "_move_no_overwrite", interrupt_after_move
            ):
                with self.assertRaises(KeyboardInterrupt):
                    executor.apply_undo(undo)

            self.assertFalse(source.exists())
            self.assertEqual(destination.read_text(encoding="utf-8"), "planned bytes")
            self.assertEqual(executor.list_history(root)[0]["status"], "applied")
            self.assertEqual(list(root.rglob(".file-organizer-quarantine-*")), [])

    def test_undo_refuses_replacement_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("original", encoding="utf-8")
            result = executor.apply_plan(create_plan(root))
            destination = root / "Documents" / "note.txt"
            destination.write_text("replacement", encoding="utf-8")

            plan = executor.create_undo_plan(root, result.operation_id)

            self.assertFalse(plan.safe_to_apply)
            self.assertTrue(
                any("Replacement file fingerprint" in item for item in plan.conflicts)
            )
            with self.assertRaises(ConflictError):
                executor.apply_undo(plan)
            self.assertEqual(destination.read_text(encoding="utf-8"), "replacement")

    def test_partial_undo_is_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "first.txt").write_text("first", encoding="utf-8")
            (root / "second.jpg").write_text("second", encoding="utf-8")
            result = executor.apply_plan(create_plan(root))
            plan = executor.create_undo_plan(root, result.operation_id)
            real_move = executor._move_no_overwrite
            calls = 0

            def fail_move_and_rollback(*args: object) -> None:
                nonlocal calls
                calls += 1
                if calls in {2, 3}:
                    raise OSError("injected partial undo")
                real_move(*args)  # type: ignore[arg-type]

            with mock.patch.object(
                executor, "_move_no_overwrite", fail_move_and_rollback
            ):
                with self.assertRaises(TransactionError) as raised:
                    executor.apply_undo(plan)

            self.assertFalse(raised.exception.rollback_succeeded)
            self.assertEqual(executor.list_history(root)[0]["status"], "undo_partial")
            retry = executor.create_undo_plan(root, result.operation_id)
            self.assertTrue(retry.safe_to_apply)
            self.assertEqual(len(retry.moves), 1)
            executor.apply_undo(retry)
            self.assertTrue((root / "first.txt").is_file())
            self.assertTrue((root / "second.jpg").is_file())
            self.assertEqual(executor.list_history(root)[0]["status"], "undone")

    def test_manifest_paths_are_treated_as_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("note", encoding="utf-8")
            result = executor.apply_plan(create_plan(root))
            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            payload["operations"][0]["source"] = "..\\outside.txt"
            result.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(HistoryError):
                executor.create_undo_plan(root, result.operation_id)

    def test_manifest_quarantine_paths_are_derived_and_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("note", encoding="utf-8")
            result = executor.apply_plan(create_plan(root))
            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            payload["operations"][0]["source_quarantine"] = "../outside/entry"
            result.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(HistoryError):
                executor.create_undo_plan(root, result.operation_id)

    def test_interrupted_quarantine_is_reported_as_an_undo_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_text("note", encoding="utf-8")
            result = executor.apply_plan(create_plan(root))
            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            relative = payload["operations"][0]["source_quarantine"]
            quarantine = root.joinpath(*relative.split("/"))
            quarantine.parent.mkdir()
            quarantine.write_text("preserved concurrent bytes", encoding="utf-8")

            undo = executor.create_undo_plan(root, result.operation_id)

            self.assertFalse(undo.safe_to_apply)
            self.assertEqual(undo.moves, ())
            self.assertTrue(
                any(
                    "preserved in quarantine" in conflict for conflict in undo.conflicts
                )
            )
            self.assertEqual(
                quarantine.read_text(encoding="utf-8"), "preserved concurrent bytes"
            )


if __name__ == "__main__":
    unittest.main()
