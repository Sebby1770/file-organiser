from pathlib import Path

from file_organiser.advise import build_advice, classify_path, format_advice, is_protected, is_safe_delete
from file_organiser.cli import build_parser, main
from file_organiser.disk import scan_usage


def test_parser_allows_no_command():
    args = build_parser().parse_args([])
    assert args.command is None


def test_main_no_args_opens_app(monkeypatch):
    called = {}

    def fake_run(**_kwargs):
        called["app"] = True
        return 0

    monkeypatch.setattr("file_organiser.launch.run_desktop", fake_run)
    assert main([]) == 0
    assert called.get("app") is True


def test_version_420():
    from file_organiser import __version__

    assert __version__ == "4.2.0"


def test_node_modules_is_delete(tmp_path: Path):
    target = tmp_path / "node_modules"
    target.mkdir()
    bucket, reason, kind = classify_path(target, is_dir=True, size=9000)
    assert bucket == "delete"
    assert kind == "cache"
    assert "regenerable" in reason.lower() or "cache" in reason.lower()
    nested = target / "pkg" / "blob.js"
    bucket2, _r, kind2 = classify_path(nested, size=9000, category="Code")
    assert bucket2 == "delete"
    assert kind2 == "cache"


def test_documents_folder_is_keep(tmp_path: Path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    bucket, _reason, kind = classify_path(docs, is_dir=True, size=100)
    assert bucket == "keep"
    assert kind in {"library", "protected"}


def test_ssh_is_protected(tmp_path: Path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    key = ssh / "id_ed25519"
    key.write_text("secret", encoding="utf-8")
    assert is_protected(ssh)
    bucket, _reason, kind = classify_path(key)
    assert bucket == "keep"
    assert kind == "protected"


def test_old_installer_in_downloads_requires_review(tmp_path: Path):
    dmg = tmp_path / "Downloads" / "Setup.dmg"
    dmg.parent.mkdir()
    dmg.write_bytes(b"x" * 64)
    bucket, reason, kind = classify_path(dmg, size=64, age_days=40)
    assert bucket == "review"
    assert kind == "installer"
    assert "installer" in reason.lower()


def test_fresh_installer_is_review(tmp_path: Path):
    dmg = tmp_path / "Downloads" / "Setup.dmg"
    bucket, _reason, kind = classify_path(dmg, size=64, age_days=2)
    assert bucket == "review"
    assert kind == "installer"


def test_junk_name_is_delete():
    bucket, _reason, kind = classify_path(Path("/tmp/.DS_Store"), size=12)
    assert bucket == "delete"
    assert kind == "junk"


def test_scan_and_advice_roundtrip(tmp_path: Path):
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "blob.js").write_bytes(b"x" * 9000)
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "notes.pdf").write_bytes(b"%PDF-1.4 " + b"n" * 200)
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    dl = tmp_path / "Downloads"
    dl.mkdir()
    old = dl / "OldApp.dmg"
    old.write_bytes(b"x" * 500)
    import os
    import time

    past = time.time() - 40 * 86400
    os.utime(old, (past, past))

    result, maps = scan_usage(tmp_path)
    assert maps.dir_size
    assert result["cache_dirs"]
    assert any(c["name"] == "node_modules" for c in result["cache_dirs"])
    assert result["junk"]

    advice = build_advice(result)
    delete_names = {row["name"] for row in advice["delete"]}
    keep_names = {row["name"] for row in advice["keep"]}
    assert "node_modules" in delete_names
    assert ".DS_Store" in delete_names
    review_names = {row["name"] for row in advice["review"]}
    assert "OldApp.dmg" in review_names
    assert "Documents" in keep_names
    assert "blob.js" not in keep_names
    assert advice["summary"]["delete_bytes"] >= 9000
    text = format_advice(advice)
    assert "DELETE" in text
    assert "KEEP" in text

    nm = tmp_path / "node_modules"
    ok, why = is_safe_delete(nm, advice)
    assert ok, why
    not_ok, _ = is_safe_delete(docs, advice)
    assert not not_ok


def test_home_is_not_safe_delete():
    ok, _why = is_safe_delete(Path.home(), {"delete": []})
    assert not ok


def test_cli_advise(tmp_path: Path, capsys):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_bytes(b"x" * 4000)
    assert main(["advise", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "delete" in out.lower() or "DELETE" in out
