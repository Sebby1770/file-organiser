"""Entry point for the packaged desktop app (double-click, no arguments)."""

from __future__ import annotations

import sys

from .launch import run_desktop

if __name__ == "__main__":
    sys.exit(run_desktop())
