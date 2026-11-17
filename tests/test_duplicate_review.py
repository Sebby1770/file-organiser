from pathlib import Path
import os

import pytest

from file_organiser.duplicate_review import DuplicateReview
from file_organiser.duplicates import find_and_report_duplicates
from rich.console import Console


@pytest.fixture
def copies(tmp_path):
    root = tmp_path.resolve()
    paths = [root / name for name in ("master.tif", "a.tmp", "b.tmp")]
    for path in paths:
        path.write_bytes(b"artwork")

    def policy(path):
        return (True, "Listed junk") if path.suffix == ".tmp" else (False, "Review only")

    review = DuplicateReview()
    group, = review.search(root, 1, policy)["groups"]
    assert group["keep"] == str(paths[0])
    assert {p.name for p in root.iterdir()} == {p.name for p in paths}
    return review, group, paths, policy


def test_purge_checks_membership_keeper_policy_and_exact_selection(copies):
    review, group, (keeper, first, second), policy = copies
    moved = []
    request = {"group": group["group"], "keep": str(keeper)}
    for paths in (None, [], [str(keeper)], [str(first), str(first)], [str(first.parent / "unknown")]):
        assert not review.purge(dict(request, paths=paths), policy, moved.append)["ok"]
    assert not review.purge(dict(request, keep=str(second), paths=[str(keeper)]), policy, moved.append)["ok"]
    assert moved == []
    request["paths"] = [str(second)]
    assert review.purge(request, policy, moved.append)["n"] == 1
    assert moved == [second]
    assert not review.purge(request, policy, moved.append)["ok"]


def test_changed_copy_blocks_group_before_any_move(copies):
    review, group, (keeper, first, second), policy = copies
    timestamp = second.stat().st_mtime_ns
    second.write_bytes(b"changed")
    os.utime(second, ns=(timestamp, timestamp))
    result = review.purge(
        {"group": group["group"], "keep": str(keeper), "paths": [str(first), str(second)]},
        policy, lambda _: pytest.fail("changed group reached Trash"),
    )
    assert not result["ok"] and "changed" in result["error"]


def test_keeper_change_mid_batch_reports_only_completed_moves(copies):
    review, group, (keeper, first, second), policy = copies

    def move(path):
        assert path == first
        path.rename(path.with_suffix(".moved"))
        keeper.write_bytes(b"changed")
        return "trashed"

    result = review.purge(
        {"group": group["group"], "keep": str(keeper), "paths": [str(first), str(second)]},
        policy, move,
    )
    assert result["n"] == 1
    assert result["skipped"][0]["path"] == str(second)
    assert second.exists()


def test_hard_links_are_counted_once_and_locked(tmp_path):
    root = tmp_path.resolve()
    first, alias = root / "original", root / "alias"
    first.write_bytes(b"data")
    try:
        os.link(first, alias)
    except OSError:
        pytest.skip("hard links unavailable")
    review = DuplicateReview()

    def eligible(_path):
        return True, "Listed for cleanup"

    assert review.search(root, 1, eligible)["groups"] == []
    (root / "copy").write_bytes(b"data")
    data = review.search(root, 1, eligible)
    group, = data["groups"]
    assert group["copies"] == 2 and group["count"] == 3
    assert data["extra_files"] == 1 and data["reclaim"] == 4
    assert {f["name"] for f in group["files"] if f["eligible"]} == {"copy"}
    result = review.purge(
        {"group": group["group"], "keep": str(root / "copy"), "paths": [str(alias)]},
        eligible, lambda _: pytest.fail("hard-linked path reached Trash"),
    )
    assert not result["ok"]


def test_cli_rechecks_content_before_removing_duplicates(tmp_path, monkeypatch):
    from file_organiser import duplicates

    root = tmp_path.resolve()
    a, b = root / "a.txt", root / "b.txt"
    a.write_text("one")
    b.write_text("two")
    monkeypatch.setattr(duplicates, "find_duplicates", lambda *args, **kw: {duplicates.file_sha256(a): [a, b]})
    with pytest.raises(OSError, match="changed since duplicate scan"):
        find_and_report_duplicates(root, Console(), delete_dupes=True, use_cache=False)
    assert a.read_text() == "one" and b.read_text() == "two"


def test_cli_preserves_hard_links_and_removes_only_separate_copy(tmp_path):
    from file_organiser.duplicates import reclaimable_bytes

    root = tmp_path.resolve()
    original, alias, separate = (root / name for name in ("master", "alias", "extra"))
    original.write_text("copy")
    try:
        os.link(original, alias)
    except OSError:
        pytest.skip("hard links unavailable")
    separate.write_text("copy")
    os.utime(original, (1000, 1000))
    assert reclaimable_bytes({"group": [original, alias, separate]}) == (4, 1)
    find_and_report_duplicates(root, Console(), delete_dupes=True, use_cache=False)
    assert not separate.exists() and original.samefile(alias)
