from pathlib import Path

from file_organiser.explain import explain_path
from file_organiser.organizer import organize
from file_organiser.rules import DEFAULT_RULES
from file_organiser.safety import dangerous_target
from file_organiser.scanner import parse_duration


def test_parse_duration():
    assert parse_duration("7d") == 7 * 86400
    assert parse_duration("2h") == 7200


def test_dangerous_home_and_root():
    assert dangerous_target(Path.home())
    assert dangerous_target(Path("/"))


def test_organize_refuses_home():
    from io import StringIO

    from rich.console import Console

    n = organize(Path.home(), DEFAULT_RULES, Console(file=StringIO()), dry_run=True)
    assert n == 0


def test_explain_png_disguised(tmp_path: Path):
    blob = tmp_path / "mystery.bin"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    info = explain_path(blob, DEFAULT_RULES, use_magic=True, use_smart=True, use_mime=False)
    assert info["final"] == "Images"
    assert info["magic"] == "Images"
