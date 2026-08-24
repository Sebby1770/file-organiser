from file_organiser.disk import candidate_roots, scan_usage, text_map


def test_scan_usage_tmp(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "big.bin").write_bytes(b"x" * 4000)
    (tmp_path / "tiny.txt").write_text("hi", encoding="utf-8")
    result = scan_usage(tmp_path)
    assert result["files"] >= 2
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
    assert any("Home" == r["label"] or "Downloads" == r["label"] for r in roots)
