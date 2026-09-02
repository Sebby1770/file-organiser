from pathlib import Path

from scripts.check_site import check_site


def test_public_site_integrity():
    root = Path(__file__).parents[1] / "docs"
    assert check_site(root) == []


def test_ga4_is_blank_and_consent_gated_by_default():
    root = Path(__file__).parents[1] / "docs"
    config = (root / "analytics-config.js").read_text(encoding="utf-8")
    script = (root / "site.js").read_text(encoding="utf-8")
    assert 'measurementId: ""' in config
    assert "validMeasurementId" in script
    assert "showAnalyticsChoice" in script
    assert "googletagmanager.com/gtag/js" in script
    assert "Desktop scans, filenames, paths, and cleanup actions are excluded" in script
