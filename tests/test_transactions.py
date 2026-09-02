"""Adversarial coverage for fingerprinted plans and filesystem transactions."""
from __future__ import annotations

from pathlib import Path

from file_organiser.organizer import build_preview_plan
from file_organiser.rules import DEFAULT_RULES, available_profiles, load_rules
from file_organiser.transaction import (
    FolderBusyError,
    FolderLock,
    build_plan,
    execute_plan,
    read_plan,
    write_plan,
)


def _plan_for(folder: Path, *, content_hash: bool = True, on_conflict: str = "rename"):
    source = folder / "note.txt"
    source.write_text("original", encoding="utf-8")
    return build_preview_plan(
        folder,
        DEFAULT_RULES,
        content_hash=content_hash,
        on_conflict=on_conflict,
    )


def test_fingerprinted_plan_apply_and_undo_roundtrip(tmp_path: Path):
    plan = _plan_for(tmp_path)
    assert plan["schema"] == "file-organiser/plan"
    assert plan["version"] == 2
    assert plan["verification"] == "sha256"

    plan_path = tmp_path.parent / "note-plan.json"
    write_plan(plan_path, plan)
    persisted = read_plan(plan_path)

    dry = execute_plan(persisted, dry_run=True)
    assert dry.ok and dry.completed == 1
    assert (tmp_path / "note.txt").exists()

    applied = execute_plan(persisted, dry_run=False)
    assert applied.ok and applied.completed == 1
    destination = tmp_path / "Documents" / "note.txt"
    assert destination.read_text(encoding="utf-8") == "original"
    assert not (tmp_path / "note.txt").exists()

    from file_organiser.history import HistoryManager

    history = HistoryManager(tmp_path).peek()
    assert history and history["id"] == applied.transaction_id

    from file_organiser.transaction import undo_latest

    undone = undo_latest(tmp_path)
    assert undone.ok and undone.completed == 1
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "original"
    assert not destination.exists()


def test_plan_rejects_content_drift_before_mutation(tmp_path: Path):
    plan = _plan_for(tmp_path, content_hash=True)
    (tmp_path / "note.txt").write_text("changed", encoding="utf-8")

    result = execute_plan(plan, dry_run=False)
    assert not result.ok
    assert "changed since plan" in result.errors[0]
    assert (tmp_path / "note.txt").exists()
    assert not (tmp_path / "Documents" / "note.txt").exists()


def test_plan_rejects_destination_traversal(tmp_path: Path):
    plan = _plan_for(tmp_path)
    plan["files"][0]["destination"] = str(tmp_path.parent / "escaped.txt")

    result = execute_plan(plan, dry_run=True)
    assert not result.ok
    assert "outside selected folder" in result.errors[0]
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_failed_batch_rolls_back_completed_moves(tmp_path: Path, monkeypatch):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("1", encoding="utf-8")
    second.write_text("2", encoding="utf-8")
    plan = build_preview_plan(tmp_path, DEFAULT_RULES, content_hash=True)

    import file_organiser.transaction as transaction

    real_move = transaction.shutil.move
    calls = 0

    def fail_second_move(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        return real_move(source, destination)

    monkeypatch.setattr(transaction.shutil, "move", fail_second_move)
    result = execute_plan(plan, dry_run=False)

    assert not result.ok and result.rolled_back
    assert all(path.exists() for path in (first, second))
    assert not (tmp_path / "Documents" / "first.txt").exists()
    assert not (tmp_path / "Documents" / "second.txt").exists()
    assert not (tmp_path / ".organizer_history.json").exists()


def test_overwrite_keeps_backup_for_safe_undo(tmp_path: Path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "Documents" / "source.txt"
    source.write_text("new", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("old", encoding="utf-8")
    plan = build_plan(
        tmp_path,
        [(source, destination, "Documents")],
        mode="move",
        on_conflict="overwrite",
        content_hash=True,
    )

    applied = execute_plan(plan, dry_run=False)
    assert applied.ok
    assert destination.read_text(encoding="utf-8") == "new"

    from file_organiser.transaction import undo_latest

    undone = undo_latest(tmp_path)
    assert undone.ok
    assert source.read_text(encoding="utf-8") == "new"
    assert destination.read_text(encoding="utf-8") == "old"


def test_named_profiles_are_discoverable_and_complete():
    profiles = available_profiles()
    names = {profile["name"] for profile in profiles}
    assert {"standard", "downloads", "minimal"}.issubset(names)
    minimal = load_rules(profile="minimal")
    assert ".pdf" in minimal["Documents"]
    assert ".png" in minimal["Media"]


def test_folder_lock_is_valid_and_excludes_concurrent_transactions(tmp_path: Path):
    with FolderLock(tmp_path):
        lock_data = (tmp_path / ".file-organiser.lock").read_text(encoding="utf-8")
        assert '"pid"' in lock_data and '"created_at"' in lock_data
        try:
            with FolderLock(tmp_path):
                raise AssertionError("a second transaction acquired the lock")
        except FolderBusyError:
            pass
    assert not (tmp_path / ".file-organiser.lock").exists()

    # A malformed lock created moments ago is treated as busy, not stale.
    (tmp_path / ".file-organiser.lock").write_text("{", encoding="utf-8")
    try:
        with FolderLock(tmp_path):
            raise AssertionError("a malformed active lock was removed")
    except FolderBusyError:
        pass
    (tmp_path / ".file-organiser.lock").unlink()


def test_empty_plan_is_a_successful_noop_without_history(tmp_path: Path):
    plan = build_plan(tmp_path, [])
    result = execute_plan(plan, dry_run=False)
    assert result.ok and result.completed == 0
    assert not (tmp_path / ".organizer_history.json").exists()


def test_cli_plan_validate_then_commit(tmp_path: Path, capsys):
    from file_organiser.cli import main

    source = tmp_path / "invoice.pdf"
    source.write_text("invoice", encoding="utf-8")
    plan_path = tmp_path.parent / "invoice-plan.json"

    assert main([
        "plan",
        str(tmp_path),
        "--profile",
        "standard",
        "--output",
        str(plan_path),
    ]) == 0
    assert plan_path.exists()
    assert main(["apply", str(plan_path)]) == 0
    assert source.exists()
    assert main(["apply", str(plan_path), "--apply"]) == 0
    assert (tmp_path / "Documents" / "invoice.pdf").exists()
    assert not source.exists()
    capsys.readouterr()
