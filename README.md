# file-organiser

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml/badge.svg)](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml)

**Smart CLI to sort, dedupe, and watch folders by type, MIME, and date.**

Sort messy downloads into category folders (`Images/`, `Documents/`, `Videos/`, …), find content duplicates by SHA-256 (parallel hashing), nest by year/month, prune empty dirs, multi-level undo, and optionally watch a folder for new files — with dry-run, stats, and rich terminal output.

## Features

| Feature | Flag / command |
|--------|-----------------|
| Organize by type | `organize FOLDER` |
| Preview only | `preview FOLDER` |
| Folder stats | `stats FOLDER` |
| Undo last run (stack) | `undo FOLDER` |
| List undo history | `undo FOLDER --list` |
| Prune empty dirs | `prune FOLDER` / `--prune-empty` |
| Recursive | `-r` / `--recursive` |
| Max scan depth | `--max-depth N` |
| MIME fallback | `--mime` |
| Copy instead of move | `--copy` |
| Date nesting `Category/YYYY/MM` | `--by-date` |
| Min size filter | `--min-size 1K` |
| Exclude globs | `--exclude "*.tmp"` |
| Conflict strategy | `--on-conflict rename\|skip\|overwrite` |
| JSON/CSV/Markdown report | `--report out.md` |
| Quiet / verbose | `-q` / `-v` |
| Find duplicates | `duplicates FOLDER` |
| Parallel hash workers | `duplicates … --workers N` |
| Delete dupes | `duplicates … --delete-dupes` |
| Watch mode | `watch FOLDER` (optional extra) |
| List rules | `categories` |
| Config discovery | `./.file-organiser.json` or XDG |

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
file-organiser preview ~/Downloads --mime          # MIME fallback for unknown extensions
file-organiser preview ~/Downloads -r --max-depth 2
```

### Organize

```bash
# Move files into category subfolders
file-organiser organize ~/Downloads

# Simulate first
file-organiser organize ~/Downloads --dry-run

# Recursive (skip folders already named like categories)
file-organiser organize ~/Downloads -r

# Limit depth (0 = top level only)
file-organiser organize ~/Downloads -r --max-depth 1

# MIME-aware categorization for extensionless / unknown files
file-organiser organize ~/Downloads --mime

# Copy instead of move
file-organiser organize ~/Downloads --copy

# Nest by modification date: Images/2024/03/photo.jpg
file-organiser organize ~/Downloads --by-date

# After moving, remove empty dirs left behind
file-organiser organize ~/Downloads -r --prune-empty

# Filters, conflict handling, reports
file-organiser organize ~/Downloads \
  --min-size 10K \
  --exclude "*.tmp" --exclude "node_modules" \
  --on-conflict rename \
  --report ~/Downloads/moves.md \
  -v
```

### Stats

```bash
file-organiser stats ~/Downloads
file-organiser stats ~/Downloads --top 20 --mime
file-organiser stats ~/Downloads --no-recursive
```

Shows total file count and size, breakdown by category (count + bytes), and the largest files.

### Undo (multi-level)

```bash
file-organiser undo ~/Downloads           # pop most recent snapshot
file-organiser undo ~/Downloads --list    # show history stack
```

Each successful `organize` **pushes** a snapshot onto a stack in `.organizer_history.json` (last 10 kept). `undo` pops the most recent. Legacy single-snapshot history files are still supported.

### Prune empty directories

```bash
file-organiser prune ~/Downloads
file-organiser prune ~/Downloads --dry-run
```

Only removes **empty** subdirectories. Never deletes the root folder or any non-empty dir. Prefer this after recursive organize when leftover empty trees remain, or use `organize --prune-empty` for move mode.

### Duplicates

```bash
# Find files with identical content (SHA-256, parallel by default)
file-organiser duplicates ~/Downloads
file-organiser duplicates ~/Downloads --workers 4

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

## Custom rules & config discovery

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

When **`--config` is omitted**, rules are discovered in order:

1. `./.file-organiser.json` (current working directory)
2. `~/.config/file-organiser/rules.json` (or `$XDG_CONFIG_HOME/file-organiser/rules.json`)
3. Built-in defaults

Unmatched extensions go to `Other/`. With `--mime`, unknown/missing extensions also try `mimetypes.guess_type` (e.g. `image/*` → Images, `application/pdf` → Documents).

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

### MIME fallback (`--mime`)

| MIME | Category |
|------|----------|
| `image/*` | Images |
| `video/*` | Videos |
| `audio/*` | Audio |
| `text/*` | Documents |
| `application/pdf` | Documents |
| `application/zip`, `application/gzip`, … | Archives |
| `application/json`, `application/javascript`, … | Code |
| `font/*`, `application/font-woff` | Fonts |

## Reports

`--report PATH` chooses format by extension:

- `.json` — structured JSON (default for unknown suffixes)
- `.csv` — CSV with source/destination columns
- `.md` / `.markdown` — Markdown table

## Project layout

```
file-organiser/
├── file_organiser/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py           # argparse entry
│   ├── organizer.py     # organize / preview / undo / stats / prune
│   ├── rules.py         # defaults, MIME map, config discovery
│   ├── history.py       # multi-level undo stack
│   ├── scanner.py       # walk, filters, max-depth, size parse
│   ├── duplicates.py    # parallel SHA-256 dupe finder
│   ├── watch.py         # optional watchdog
│   └── report.py        # JSON/CSV/Markdown reports
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
- `prune` / `--prune-empty` only remove **empty** directories.
- Prefer `--dry-run` before destructive runs (especially `--delete-dupes`).

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest tests/ -q
```

CI runs pytest on Python 3.11 and 3.12 via GitHub Actions.

## License

MIT — see [LICENSE](LICENSE).
