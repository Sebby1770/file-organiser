#!/usr/bin/env python3
"""Compatibility entry point; prefer the installed ``file-organizer`` command."""

from file_organizer.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
