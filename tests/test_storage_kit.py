import json
from zipfile import ZipFile

from file_organiser.disk import scan_usage
from file_organiser.scan_report import storage_snapshot
from scripts.build_review_kit import build


def test_snapshot_omits_private_strings_and_preserves_totals(tmp_path):
    root = tmp_path / "private-client"
    root.mkdir()
    (root / "confidential.mp4").write_bytes(b"video")
    (root / "sensitive.custom").write_bytes(b"private")
    result, _ = scan_usage(root, rules={"Videos": [".mp4"], "Secret project": [".custom"]})
    result["errors"] = 2
    snapshot = storage_snapshot(result)
    assert snapshot["captured"] == result["captured_at"]
    assert snapshot["errors"] == 2
    assert snapshot["groups"] == [
        {"type": "Other", "bytes": 7, "files": 1},
        {"type": "Videos", "bytes": 5, "files": 1},
    ]
    output = json.dumps(snapshot)
    for private in (str(root), "confidential", "sensitive", "Secret project"):
        assert private not in output


def test_delivery_contains_offline_tool_and_reproducible_worked_example(tmp_path):
    first = build(tmp_path / "first.zip")
    second = build(tmp_path / "second.zip")
    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        html = archive.read("Storage Review Kit.html").decode()
        assert 'data-mode="offline"' in html and "connect-src 'none'" in html
        assert "<script src=" not in html and 'id="offer"' not in html
        before, after = [json.loads(archive.read(name)) for name in sorted(archive.namelist()) if name.endswith(".json")]
        for sample in (before, after):
            assert sample["source"] == "browser"
        def total(sample):
            return sum(row["bytes"] for row in sample["groups"])
        assert total(after) - total(before) == 8 * 1024**3
        assert "LICENSE.txt" in archive.namelist() and "START HERE.md" in archive.namelist()
