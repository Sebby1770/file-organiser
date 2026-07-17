from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "file_organizer", *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class CliTests(unittest.TestCase):
    def test_plan_json_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "notes.txt"
            source.write_text("important", encoding="utf-8")

            result = run_cli("plan", str(root), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["kind"], "organization-plan")
            self.assertEqual(payload["summary"]["planned_moves"], 1)
            self.assertTrue(source.is_file())
            self.assertFalse((root / "Documents").exists())
            self.assertFalse((root / ".file-organizer").exists())

    def test_json_flag_is_supported_before_and_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "notes.txt").write_text("important", encoding="utf-8")

            for arguments in (
                ("--json", "plan", str(root)),
                ("plan", str(root), "--json"),
            ):
                with self.subTest(arguments=arguments):
                    result = run_cli(*arguments)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        json.loads(result.stdout)["kind"], "organization-plan"
                    )
                    self.assertEqual(result.stderr, "")

    def test_apply_history_dry_run_and_undo_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "photo.jpg"
            source.write_bytes(b"jpeg bytes")

            applied = run_cli("apply", str(root), "--json")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_payload = json.loads(applied.stdout)
            operation_id = applied_payload["operation_id"]
            destination = root / "Images" / "photo.jpg"
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"jpeg bytes")

            history = run_cli("history", str(root), "--json")
            self.assertEqual(history.returncode, 0, history.stderr)
            history_payload = json.loads(history.stdout)
            self.assertEqual(
                history_payload["operations"][0]["operation_id"], operation_id
            )

            preview = run_cli(
                "undo", str(root), "--operation", operation_id, "--dry-run", "--json"
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertTrue(json.loads(preview.stdout)["safe_to_apply"])
            self.assertTrue(destination.is_file())

            undone = run_cli("undo", str(root), "--operation", operation_id, "--json")
            self.assertEqual(undone.returncode, 0, undone.stderr)
            self.assertEqual(json.loads(undone.stdout)["status"], "undone")
            self.assertEqual(source.read_bytes(), b"jpeg bytes")
            self.assertFalse(destination.exists())

    def test_organize_requires_explicit_apply_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "script.py"
            source.write_text("print('safe')\n", encoding="utf-8")

            preview = run_cli("organize", str(root))
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertIn("Preview only", preview.stdout)
            self.assertTrue(source.exists())

            applied = run_cli("organize", str(root), "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue((root / "Code" / "script.py").is_file())

    def test_duplicate_report_is_content_based_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"identical")
            second.write_bytes(b"identical")

            result = run_cli("duplicates", str(root), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["summary"]["duplicate_groups"], 1)
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())
            self.assertFalse((root / ".file-organizer").exists())

    def test_json_errors_are_machine_readable_and_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            result = run_cli("plan", str(missing), "--json")
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["kind"], "error")
            self.assertIn("does not exist", payload["error"]["message"])

    def test_json_usage_errors_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cases = (
                ("--json",),
                ("--json", "plan"),
                ("plan", "--json"),
                ("--json", "plan", str(root), "--collision", "explode"),
                ("plan", str(root), "--json", "--unknown-option"),
            )

            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = run_cli(*arguments)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    payload = json.loads(result.stderr)
                    self.assertEqual(payload["kind"], "error")
                    self.assertEqual(payload["error"]["type"], "UsageError")
                    self.assertTrue(payload["error"]["message"])
                    self.assertTrue(payload["error"]["usage"].startswith("usage: "))


if __name__ == "__main__":
    unittest.main()
