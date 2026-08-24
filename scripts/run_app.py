"""PyInstaller / double-click entry. Opens the desktop app with no arguments."""
from __future__ import annotations

import sys

from file_organiser.launch import run_desktop

if __name__ == "__main__":
    sys.exit(run_desktop())
