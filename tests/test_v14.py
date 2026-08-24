from pathlib import Path

from file_organiser.magic import sniff_category
from file_organiser.organizer import apply_saved_plan, build_preview_plan
from file_organiser.rules import DEFAULT_RULES, category_for_path, load_rules
from file_organiser.smart import smart_category


def test_new_categories_exist():
    rules = load_rules(None)
    assert ".epub" in rules["Ebooks"]
    assert ".heic" in rules["Images"]
    assert ".psd" in rules["Design"]


def test_magic_png_and_pdf(tmp_path: Path):
    png = tmp_path / "lying.bin"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    pdf = tmp_path / "also.bin"
    pdf.write_bytes(b"%PDF-1.4\n%")
    assert sniff_category(png) == "Images"
    assert sniff_category(pdf) == "Documents"
    assert category_for_path(png, DEFAULT_RULES, use_magic=True) == "Images"


def test_smart_screenshot_and_invoice():
    assert smart_category(Path("Screenshot 2026-08-01.png")) == "Images"
    assert smart_category(Path("IMG_4032")) == "Images"
    assert smart_category(Path("Q3-invoice")) == "Documents"
    assert category_for_path(Path("Screenshot 2026-08-01"), DEFAULT_RULES, use_smart=True) == "Images"


def test_preview_plan_and_apply_dry_run(tmp_path: Path):
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff\x00")
    (tmp_path / "notes.pdf").write_bytes(b"%PDF-1.4")
    plan = build_preview_plan(tmp_path, DEFAULT_RULES, use_magic=True)
    assert plan["count"] == 2
    cats = {row["category"] for row in plan["files"]}
    assert "Images" in cats and "Documents" in cats
    plan_path = tmp_path / "plan.json"
    import json

    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    class Dummy:
        def print(self, *args, **kwargs):
            pass

    n = apply_saved_plan(plan_path, Dummy(), dry_run=True)
    assert n == 2
    assert (tmp_path / "photo.jpg").exists()
