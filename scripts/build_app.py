"""Build a one-folder desktop binary with PyInstaller."""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import PyInstaller.__main__
except ImportError:
    sys.stderr.write("pip install pyinstaller\n")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[1]
sep = ";" if os.name == "nt" else ":"
src = ROOT / "file_organiser" / "static"
PyInstaller.__main__.run(
    [
        "--noconfirm",
        "--clean",
        "--name",
        "FileOrganiser",
        "--add-data",
        f"{src}{sep}file_organiser/static",
        "--console",
        str(ROOT / "file_organiser" / "__main__.py"),
    ]
)
print("Binary under dist/FileOrganiser/")
