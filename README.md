# File Organiser

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml/badge.svg)](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml)

**A desktop app that scans your computer and tells you what to delete, what to review, and what to keep.**

Site: [https://sebby1770.github.io/file-organiser/](https://sebby1770.github.io/file-organiser/)

This is an app, not a terminal exercise. Double-click it. Press **Scan this computer**. Read three columns — Delete, Review, Keep — each row has a reason. Trash only what you check. Nothing is uploaded.

DaisyDisk draws a ring. File Organiser draws the ring **and** decides: caches and old installers on Delete, large stale files on Review, photos/documents/keys on Keep.

## Get it (no Terminal)

1. Download the latest build for your OS: [GitHub Releases](https://github.com/Sebby1770/file-organiser/releases/latest)
2. Double-click **File Organiser**
3. Press **Scan this computer** (or Downloads, or Choose folder)

macOS source checkout: double-click `scripts/Open File Organiser.command` after Python 3 is installed.

GitHub Actions workflow `release-app` builds macOS, Windows, and Linux binaries.

## What the advice means

| Column | Meaning | Bulk trash? |
|--------|---------|-------------|
| **Delete** | Regenerable caches (`node_modules`, `__pycache__`, browser caches), junk (`.DS_Store`), empty files, old installers in Downloads | Yes, after you confirm |
| **Review** | Large stale files, duplicates, build folders (`dist`/`build`), recent installers | No — look first |
| **Keep** | Pictures, Documents, Movies, Mail, Photos libraries, `.ssh`, git | Locked |

The app will not trash `$HOME` or `/` as a whole. Keys and photo libraries are protected.

## Run from Python (still an app)

```bash
python3 -m pip install -e ".[trash]"
python3 -m file_organiser
```

No subcommand. That **opens the app**. Same as `file-organiser app`.

```bash
python3 -m file_organiser advise ~/Downloads   # text report: delete / review / keep
python3 -m file_organiser map ~/Downloads      # text sunburst cousin
```

Build a standalone binary:

```bash
python3 -m pip install pyinstaller
python3 scripts/build_app.py
# double-click dist/FileOrganiser
```

## CLI (optional)

Power-user commands still exist: `organize`, `preview`, `duplicates`, `clean`, `undo`, `why`, `doctor`, `watch`. Dry-run is the default for destructive work. See `--help` after you actually want a command.

| Feature | Flag / command |
|--------|-----------------|
| Open the app | *(no args)* / `app` |
| Delete vs keep report | `advise FOLDER` |
| Disk map (text) | `map FOLDER` |
| Organize by type | `organize FOLDER` |
| Preview only | `preview FOLDER` |
| Find duplicates | `duplicates FOLDER` |
| Undo last run | `undo FOLDER` |
| Clean junk | `clean FOLDER` (`--apply` to delete) |

## Safe transaction workflow

Organising is now a validated transaction. A normal `organize` command is a
dry-run unless you explicitly pass `--apply`:

```bash
file-organiser profiles
file-organiser organize ~/Downloads --profile downloads --dry-run
file-organiser organize ~/Downloads --profile downloads --apply
file-organiser undo ~/Downloads --dry-run
file-organiser undo ~/Downloads
```

For a reviewable, repeatable workflow, create a plan first. Plans use SHA-256
fingerprints by default, are written atomically, and are checked again while a
per-folder lock is held. Any failure rolls back moves already made in that
batch; overwritten destinations are kept as private backups until undo:

```bash
file-organiser plan ~/Downloads --profile downloads -o /tmp/downloads-plan.json
file-organiser apply /tmp/downloads-plan.json          # validate only
file-organiser apply /tmp/downloads-plan.json --apply  # commit
```

Use `--fast-fingerprint` with `plan` only when scan speed matters more than
content-level drift detection; it records size and modification time instead.
JSON and Markdown reports include the transaction id and whether the result was
planned, committed, or rolled back; CSV keeps stable move columns. Paths, categories, symlink components, and stale
source/destination fingerprints are revalidated before anything is changed.

Built-in profiles are `standard`, `downloads`, and `minimal`. A custom rules
file can expose named profiles with this shape:

```json
{
  "version": 1,
  "default_profile": "work",
  "profiles": {
    "work": {
      "description": "Project files",
      "rules": {"Documents": [".pdf"], "Code": [".py", ".ts"]}
    }
  }
}
```

Site and source: [GitHub](https://github.com/Sebby1770/file-organiser). MIT.
