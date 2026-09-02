"""Build an unsigned local desktop app with PyInstaller."""

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
args = [
    "--noconfirm",
    "--clean",
    "--name",
    "FileOrganiser",
    "--windowed",
    "--add-data",
    f"{src}{sep}file_organiser/static",
    "--hidden-import",
    "send2trash",
    "--hidden-import",
    "file_organiser.advise",
    "--hidden-import",
    "file_organiser.launch",
    "--paths",
    str(ROOT),
    str(ROOT / "scripts" / "run_app.py"),
]
if sys.platform == "darwin":
    args.extend(["--osx-bundle-identifier", "com.sebby1770.fileorganiser"])
PyInstaller.__main__.run(args)
print("Built an unsigned evaluator app in dist/. Platform security warnings may appear.")
