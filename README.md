# file-organiser

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml/badge.svg)](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml)

**Smart CLI to sort, dedupe, and watch folders by type and date.**

Sort messy downloads into category folders (`Images/`, `Documents/`, `Videos/`, …), find content duplicates by SHA-256, nest by year/month, and optionally watch a folder for new files — with dry-run, undo, and rich terminal output.

## Features

| Feature | Flag / command |
|--------|-----------------|
| Organize by type | `organize FOLDER` |
| Preview only | `preview FOLDER` |
| Undo last run | `undo FOLDER` |
| Recursive | `-r` / `--recursive` |
| Copy instead of move | `--copy` |
| Date nesting `Category/YYYY/MM` | `--by-date` |
| Min size filter | `--min-size 1K` |
| Exclude globs | `--exclude "*.tmp"` |
| Conflict strategy | `--on-conflict rename\|skip\|overwrite` |
| JSON/CSV report | `--report out.json` |
| Quiet / verbose | `-q` / `-v` |
| Find duplicates | `duplicates FOLDER` |
| Delete dupes | `duplicates … --delete-dupes` |
| Watch mode | `watch FOLDER` (optional extra) |
| List rules | `categories` |

## Install

```bash
# From the repo (editable)
python -m pip install -e .

# With optional folder watching
python -m pip install -e ".[watch]"

# With test deps
python -m pip install -e ".[test]"
```

Console scripts (both spellings work):

```bash
file-organiser --help
file-organizer --help
# or
python -m file_organiser --help
```

## Usage

### Preview

```bash
file-organiser preview ~/Downloads
```

```
┌──────────────── Preview: /Users/you/Downloads ────────────────┐
│ Category      │ Count │ Example files                         │
├───────────────┼───────┼───────────────────────────────────────┤
│ Documents     │     4 │ report.pdf, notes.txt, ...            │
│ Images        │    12 │ photo.jpg, screenshot.png, ...        │
│ Videos        │     2 │ clip.mp4, demo.mov                    │
└───────────────┴───────┴───────────────────────────────────────┘
Total: 18 file(s) across 3 categor(ies)
```

### Organize

```bash
# Move files into category subfolders
file-organiser organize ~/Downloads

# Simulate first
file-organiser organize ~/Downloads --dry-run

# Recursive (skip folders already named like categories)
file-organiser organize ~/Downloads -r

# Copy instead of move
file-organiser organize ~/Downloads --copy

# Nest by modification date: Images/2024/03/photo.jpg
file-organiser organize ~/Downloads --by-date

# Filters & conflict handling
file-organiser organize ~/Downloads \
  --min-size 10K \
  --exclude "*.tmp" --exclude "node_modules" \
  --on-conflict rename \
  --report ~/Downloads/moves.json \
  -v
```

### Undo

```bash
file-organiser undo ~/Downloads
```

Restores the last organize (or removes copies if you used `--copy`). History lives in `.organizer_history.json` inside the folder.

### Duplicates

```bash
# Find files with identical content (SHA-256)
file-organiser duplicates ~/Downloads

# Dry-run delete (keep oldest of each group)
file-organiser duplicates ~/Downloads --delete-dupes --dry-run

# Keep newest, actually delete
file-organiser duplicates ~/Downloads --delete-dupes --keep newest
```

### Watch (optional)

```bash
pip install file-organiser[watch]
file-organiser watch ~/Downloads --by-date
```

If `watchdog` is missing, the command prints an install hint.

### Categories

```bash
file-organiser categories
file-organiser categories --config rules.example.json
```

## Custom rules

JSON object mapping category → list of extensions (dot optional, case-insensitive):

```json
{
  "Images": [".jpg", ".png", ".gif"],
  "Documents": [".pdf", ".docx", ".txt"],
  "Code": [".py", ".js", ".ts"]
}
```

```bash
file-organiser organize ~/Downloads --config rules.example.json
```

Unmatched extensions go to `Other/`.

### Default categories

| Category | Examples |
|----------|----------|
| Images | `.jpg` `.png` `.gif` `.webp` … |
| Documents | `.pdf` `.docx` `.txt` `.md` … |
| Spreadsheets | `.xlsx` `.csv` … |
| Presentations | `.pptx` `.key` … |
| Videos | `.mp4` `.mov` `.mkv` … |
| Audio | `.mp3` `.wav` `.flac` … |
| Archives | `.zip` `.tar` `.gz` … |
| Code | `.py` `.js` `.ts` `.html` … |
| Executables | `.exe` `.dmg` … |
| Fonts | `.ttf` `.woff` … |
| Other | anything else |

## Project layout

```
file-organiser/
├── file_organiser/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py           # argparse entry
│   ├── organizer.py     # organize / preview / undo
│   ├── rules.py         # defaults + config loader
│   ├── history.py       # undo history
│   ├── scanner.py       # walk, filters, size parse
│   ├── duplicates.py    # SHA-256 dupe finder
│   ├── watch.py         # optional watchdog
│   └── report.py        # JSON/CSV reports
├── tests/
├── pyproject.toml
├── rules.example.json
├── LICENSE
└── README.md
```

## Safety

- Does **not** follow symlinks (avoids loops).
- Skips **hidden** files/dirs (names starting with `.`) and the history file.
- Recursive mode **skips** existing category folders so files are not re-sorted endlessly.
- Default conflict strategy **renames** (`photo (1).jpg`) — nothing overwritten unless you pass `--on-conflict overwrite`.
- Prefer `--dry-run` before destructive runs (especially `--delete-dupes`).

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest tests/ -q
```

CI runs pytest on Python 3.11 and 3.12 via GitHub Actions.

## License

MIT — see [LICENSE](LICENSE).
