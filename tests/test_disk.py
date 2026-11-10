from file_organiser.disk import candidate_roots, scan_usage, text_map


def test_scan_usage_tmp(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "big.bin").write_bytes(b"x" * 4000)
    (tmp_path / "tiny.txt").write_text("hi", encoding="utf-8")
    result, maps = scan_usage(tmp_path)
    assert result["files"] >= 2
    assert maps.dir_size
    assert result["size"] >= 4000
    assert result["tree"]["children"]
    names = {c["name"] for c in result["tree"]["children"]}
    assert "a" in names or "tiny.txt" in names
    assert result["largest"]
    chart = text_map(result)
    assert "tiny.txt" in chart or "a" in chart


def test_candidate_roots_include_home():
    roots = candidate_roots()
    assert roots
    assert any(r["label"] in {"Home folder", "Home", "Downloads"} for r in roots)


def test_scan_counts_node_modules_as_waste(tmp_path):
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "blob.js").write_bytes(b"x" * 8000)
    result, _maps = scan_usage(tmp_path, skip_hidden=True)
    assert result["waste"]["caches"] >= 8000
    names = {c["name"] for c in result["tree"]["children"]}
    assert "node_modules" in names


def test_hidden_items_match_scan_and_map_scope(tmp_path):
    hidden = tmp_path / ".private"
    hidden.mkdir()
    (hidden / "secret.txt").write_bytes(b"private")
    sub = tmp_path / "notes"
    sub.mkdir()
    (sub / ".hidden.txt").write_bytes(b"hidden")
    (tmp_path / ".DS_Store").write_bytes(b"junk")

    result, _ = scan_usage(tmp_path)
    assert result["size"] == 4
    nodes = {node["name"]: node for node in result["tree"]["children"]}
    assert nodes.keys() == {"notes", ".DS_Store"}
    assert not nodes["notes"]["children"]
    assert not result["empty_dirs"]

    full, _ = scan_usage(tmp_path, skip_hidden=False)
    assert full["size"] == 17
    assert ".private" in {node["name"] for node in full["tree"]["children"]}


def test_stopping_mid_directory_discards_partial_result(tmp_path):
    import pytest
    from file_organiser.disk import ScanCancelled

    for i in range(20):
        (tmp_path / f"{i}.txt").touch()
    responses = iter([False, False, True])
    with pytest.raises(ScanCancelled):
        scan_usage(tmp_path, cancel=lambda: next(responses))


def test_unreadable_subfolder_is_counted(tmp_path, monkeypatch):
    import os

    private = tmp_path / "unreadable"
    private.mkdir()
    scandir = os.scandir

    def restricted(path):
        if str(path) == str(private):
            raise PermissionError(13, "Access denied", str(path))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", restricted)
    result, _ = scan_usage(tmp_path)
    assert result["errors"] == 1
    assert not result["empty_dirs"]


def test_space_estimates_include_all_stale_files_and_exclude_overlap(tmp_path):
    from file_organiser.advise import build_advice

    cache = tmp_path / "node_modules"
    cache.mkdir()
    (cache / "bytecode.pyc").write_bytes(b"abc")
    for index in range(55):
        (tmp_path / f"{index}.bin").write_bytes(b"x")
    result, _ = scan_usage(tmp_path, stale_min_bytes=1, stale_days=0)
    assert len(result["stale"]) == 50
    assert result["waste"]["stale"] == 58
    assert result["waste"]["total"] == result["size"] == 58
    assert build_advice(result)["summary"]["delete_bytes"] == 3
