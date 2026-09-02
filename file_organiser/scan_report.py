"""Portable, local-only exports of a File Organiser scan and its advice."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from . import __version__


def build_scan_report(result: dict[str, Any], advice: dict[str, Any]) -> dict[str, Any]:
    """Return a stable, JSON-safe report payload.

    Reports intentionally contain paths because their purpose is to support a
    cleanup decision. They are created only after an explicit export click and
    never leave the machine automatically.
    """
    rows: list[dict[str, Any]] = []
    for bucket in ("delete", "review", "keep"):
        for item in advice.get(bucket, []) or []:
            rows.append(
                {
                    "bucket": bucket,
                    "name": str(item.get("name") or ""),
                    "path": str(item.get("path") or ""),
                    "bytes": int(item.get("size") or 0),
                    "size": str(item.get("label") or ""),
                    "kind": str(item.get("kind") or ""),
                    "confidence": str(item.get("confidence") or ""),
                    "reason": str(item.get("reason") or ""),
                }
            )
    return {
        "schema": "file-organiser.scan-report.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": __version__,
        "root": str(result.get("root") or ""),
        "files_scanned": int(result.get("files") or result.get("visited") or 0),
        "bytes_scanned": int(result.get("size") or 0),
        "summary": dict(advice.get("summary") or {}),
        "items": rows,
        "privacy": "Created locally. Paths may be sensitive; share this file only if you intend to.",
    }


def report_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def report_csv(payload: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    fields = ["bucket", "name", "path", "bytes", "size", "kind", "confidence", "reason"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(payload.get("items") or [])
    return output.getvalue().encode("utf-8")
