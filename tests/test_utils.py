from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import file_organizer.utils as utils


class AtomicWriteTests(unittest.TestCase):
    def test_private_write_succeeds_without_fchmod(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "history.json"

            with mock.patch.object(utils.os, "fchmod", None, create=True):
                utils.atomic_write_json(target, {"status": "applied"}, private=True)

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"status": "applied"},
            )
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_pre_fdopen_failure_closes_descriptor_and_preserves_error(self) -> None:
        real_close = utils.os.close
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "history.json"

            with (
                mock.patch.object(
                    utils.os,
                    "fchmod",
                    side_effect=RuntimeError("mode failed"),
                    create=True,
                ),
                mock.patch.object(
                    utils.os, "close", wraps=real_close
                ) as close_descriptor,
                mock.patch.object(
                    Path, "unlink", side_effect=PermissionError("locked")
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "mode failed"):
                    utils.atomic_write_json(target, {}, private=True)

            close_descriptor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
