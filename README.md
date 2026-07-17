# file-organiser

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml/badge.svg)](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml)

**Smart CLI to sort, dedupe, clean, rename, and watch folders by type, MIME, and date.**

Sort messy downloads into category folders (`Images/`, `Documents/`, `Videos/`, …), find content duplicates by SHA-256 (cached + parallel hashing with reclaimable-space report), bulk-rename, clean junk, compare folders, nest by year/month, prune empty dirs, multi-level undo, search by category/ext/name, and optionally watch a folder for new files — with dry-run, JSON plans, stats, and rich terminal output (respects `NO_COLOR`).

## Features

| Feature | Flag / command |
|--------|-----------------|
| Organize by type | `organize FOLDER` |
| Interactive categorize | `organize FOLDER --interactive` |
| Preview only | `preview FOLDER` |
| Machine-readable plan | `preview FOLDER --json` |
| Find by category/ext/name | `find FOLDER --category Images` |
| Category tree | `tree FOLDER` |
| Extension inventory | `extensions FOLDER` |
| Folder stats | `stats FOLDER` |
| Undo last run (stack) | `undo FOLDER` |
| List undo history | `undo FOLDER --list` |
| Prune empty dirs | `prune FOLDER` / `--prune-empty` |
| Clean junk / empty files | `clean FOLDER` (`--apply` to delete) |
| Bulk rename | `rename FOLDER --pattern "…" --match "*.jpg"` |
| Slug rename | `rename FOLDER --slug` |
| Folder diff | `diff FOLDER_A FOLDER_B` |
| Init config | `init-config` / `init-config --local` |
| Benchmark | `bench FOLDER` |
| Recursive | `-r` / `--recursive` |
| Max scan depth | `--max-depth N` |
| MIME fallback | `--mime` |
| Copy instead of move | `--copy` |
| Symlink instead of move | `--symlink` |
| Date nesting `Category/YYYY/MM` | `--by-date` |
| Min size filter | `--min-size 1K` |
| Exclude globs | `--exclude "*.tmp"` |
| Include globs | `--include "*.pdf"` |
| Conflict strategy | `--on-conflict rename\|skip\|overwrite` |
| JSON/CSV/Markdown report | `--report out.md` |
| Quiet / verbose | `-q` / `-v` |
| Find duplicates | `duplicates FOLDER` |
| Reclaimable space report | always printed after dupe scan |
| Hash cache (auto) | `.organizer_hash_cache.json` |
| Parallel hash workers | `duplicates … --workers N` |
| Delete dupes | `duplicates … --delete-dupes` |
| Trash instead of delete | `duplicates … --delete-dupes --trash` |
| Watch mode | `watch FOLDER` (optional extra) |
| List rules | `categories` |
| Config discovery | `./.file-organiser.json` or XDG |
| Colour-free output | `NO_COLOR=1` |

## Install

```bash
# From the repo (editable)
python -m pip install -e .

# With optional folder watching
python -m pip install -e ".[watch]"

# With safe trash for duplicate deletion
python -m pip install -e ".[trash]"

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
file-organiser preview ~/Downloads --json          # machine-readable plan
```

`preview --json` prints a plan to stdout:

```json
{
  "folder": "/path/to/Downloads",
  "count": 2,
  "files": [
    {
      "source": "/path/to/Downloads/photo.jpg",
      "destination": "/path/to/Downloads/Images/photo.jpg",
      "category": "Images"
    }
  ]
}
```

### Find

```bash
file-organiser find ~/Downloads --category Images
file-organiser find ~/Downloads --ext .pdf
file-organiser find ~/Downloads --name "*.invoice*" -r
file-organiser find ~/Downloads --ext .png --min-size 100K
```

### Tree

```bash
file-organiser tree ~/Downloads
file-organiser tree ~/Downloads --no-recursive
```

Shows a category-folder tree with file counts and total sizes (including date nests when present).

### Extensions inventory

```bash
file-organiser extensions ~/Downloads
file-organiser extensions ~/Downloads --no-recursive
```

Table of every extension with count and total bytes, sorted by size descending.

### Organize

```bash
# Move files into category subfolders
file-organiser organize ~/Downloads

# Simulate first
file-organiser organize ~/Downloads --dry-run

# Interactive: prompt for category on each Other/unknown file
file-organiser organize ~/Downloads --interactive

# Recursive (skip folders already named like categories)
file-organiser organize ~/Downloads -r

# Limit depth (0 = top level only)
file-organiser organize ~/Downloads -r --max-depth 1

# MIME-aware categorization for extensionless / unknown files
file-organiser organize ~/Downloads --mime

# Copy instead of move
file-organiser organize ~/Downloads --copy

# Symlink into category folders (sources stay put; scan never follows links)
file-organiser organize ~/Downloads --symlink

# Nest by modification date: Images/2024/03/photo.jpg
file-organiser organize ~/Downloads --by-date

# After moving, remove empty dirs left behind
file-organiser organize ~/Downloads -r --prune-empty

# Filters, conflict handling, reports
file-organiser organize ~/Downloads \
  --min-size 10K \
  --exclude "*.tmp" --exclude "node_modules" \
  --include "*.pdf" --include "*.docx" \
  --on-conflict rename \
  --report ~/Downloads/moves.md \
  -v
```

### Clean (junk cleanup)

Always **dry-run** unless you pass `--apply`. Never deletes history or hash-cache files.

```bash
# Preview junk: empty files, .DS_Store, Thumbs.db, desktop.ini, *~, *.tmp, …
file-organiser clean ~/Downloads

# Actually delete
file-organiser clean ~/Downloads --apply

# Skip 0-byte files; only name-based junk
file-organiser clean ~/Downloads --no-empty-files --apply

# Custom junk patterns
file-organiser clean ~/Downloads --junk "*.log" --junk "._*" --apply
```

### Bulk rename

Dry-run by default; `--apply` to execute. Renames are recorded in history for `undo`.

```bash
# Pattern tokens: {n}, {n:04d}, {name}, {stem}, {ext}, {ext_no_dot}
file-organiser rename ~/Photos --pattern "IMG_{n:04d}{ext}" --match "*.jpg"
file-organiser rename ~/Photos --pattern "IMG_{n:04d}{ext}" --match "*.jpg" --apply

# Slugify: lowercase + spaces → hyphens
file-organiser rename ~/Downloads --slug --apply
```

### Diff (compare two folders)

```bash
file-organiser diff ~/Backup ~/Documents
file-organiser diff ./folder_a ./folder_b --no-recursive
```

Reports:

- **Only in A** / **Only in B** (relative paths)
- **Same name, different content** (hash mismatch)
- **Identical** (same relative path + same SHA-256)

Useful for backup checks.

### Init config

```bash
# Write ~/.config/file-organiser/rules.json (XDG)
file-organiser init-config

# Write ./.file-organiser.json in the current directory
file-organiser init-config --local

# Overwrite existing
file-organiser init-config --force
```

The file is a JSON object of category → extension lists (same format as custom `--config`). See **Custom rules** below.

### Benchmark

```bash
file-organiser bench ~/Downloads
file-organiser bench ~/Downloads --limit 50
```

Times a folder scan and SHA-256 hashing of the first N files; prints throughput (files/s, MiB/s).

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

Each successful `organize` / `rename --apply` **pushes** a snapshot onto a stack in `.organizer_history.json` (last 10 kept). `undo` pops the most recent. Works for move, copy, symlink, and rename modes. Legacy single-snapshot history files are still supported.

### Prune empty directories

```bash
file-organiser prune ~/Downloads
file-organiser prune ~/Downloads --dry-run
```

Only removes **empty** subdirectories. Never deletes the root folder or any non-empty dir. Prefer this after recursive organize when leftover empty trees remain, or use `organize --prune-empty` for move mode.

### Duplicates

```bash
# Find files with identical content (SHA-256, parallel + hash cache)
# Always prints reclaimable space if all but the keeper were deleted
file-organiser duplicates ~/Downloads
file-organiser duplicates ~/Downloads --workers 4

# Dry-run delete (keep oldest of each group)
file-organiser duplicates ~/Downloads --delete-dupes --dry-run

# Keep newest, actually delete
file-organiser duplicates ~/Downloads --delete-dupes --keep newest

# Prefer OS trash (requires send2trash)
pip install file-organiser[trash]
file-organiser duplicates ~/Downloads --delete-dupes --trash

# Skip the on-disk hash cache
file-organiser duplicates ~/Downloads --no-cache
```

Hash results are cached in `.organizer_hash_cache.json` inside the scanned folder. Entries are reused when path + mtime + size are unchanged, so re-runs are much faster.

After every scan (even without `--delete-dupes`), the tool prints **reclaimable** bytes and the number of extra copies that could be removed while keeping one file per group.

If `--trash` is set but `send2trash` is not installed, the tool **falls back to permanent delete** and prints a warning with the install hint.

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

### Colour / NO_COLOR

Set `NO_COLOR` (any non-empty value) to disable Rich colour output — useful for logs and CI:

```bash
NO_COLOR=1 file-organiser stats ~/Downloads
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
file-organiser init-config --local   # create ./.file-organiser.json from defaults
```

When **`--config` is omitted**, rules are discovered in order:

1. `./.file-organiser.json` (current working directory)
2. `~/.config/file-organiser/rules.json` (or `$XDG_CONFIG_HOME/file-organiser/rules.json`)
3. Built-in defaults

Unmatched extensions go to `Other/`. With `--mime`, unknown/missing extensions also try `mimetypes.guess_type` (e.g. `image/*` → Images, `application/pdf` → Documents). With `organize --interactive`, each Other file is prompted for a category (default Other; type `skip` to leave it).

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
│   ├── organizer.py     # organize / preview / find / tree / extensions / undo / stats / prune
│   ├── rules.py         # defaults, MIME map, config discovery, init-config
│   ├── history.py       # multi-level undo stack
│   ├── scanner.py       # walk, filters, include/exclude, max-depth, size parse
│   ├── duplicates.py    # parallel SHA-256 dupe finder + hash cache + trash + reclaimable
│   ├── clean.py         # junk / empty-file cleanup
│   ├── rename.py        # bulk rename + slug
│   ├── diff.py          # folder comparison
│   ├── bench.py         # scan + hash benchmark
│   ├── console_util.py  # NO_COLOR-aware Console
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
- Skips **hidden** files/dirs (names starting with `.`) and internal metadata (history, hash cache) during organize/scan — except `clean`, which can target known junk names like `.DS_Store` while still protecting history/hash cache.
- Recursive mode **skips** existing category folders so files are not re-sorted endlessly.
- Default conflict strategy **renames** (`photo (1).jpg`) — nothing overwritten unless you pass `--on-conflict overwrite`.
- `prune` / `--prune-empty` only remove **empty** directories.
- `clean` and `rename` are **dry-run by default**; pass `--apply` to execute.
- Prefer `--dry-run` before destructive runs (especially `--delete-dupes`).
- Prefer `--trash` with `file-organiser[trash]` so duplicates go to the OS recycle bin.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest tests/ -q
```

CI runs pytest on Python 3.11 and 3.12 via GitHub Actions.

## License

MIT — see [LICENSE](LICENSE).
