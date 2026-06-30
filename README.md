# Smart File Organizer CLI

A clean Python command-line tool that automatically sorts files in a folder by type: images, documents, videos, code, and more. Built with `argparse` and [Rich](https://github.com/Textualize/rich) for colorful output and progress bars.

The refresh turns the project into a more operations-safe organiser: proper package layout, recursive scans, date partitioning, quarantine mode, JSONL audit logs, max-file safety limits, tests, packaging metadata, CI, and a changelog.

## Features

- Three commands: `organize`, `preview`, `undo`
- Colored terminal output with tables and progress bars
- Dry-run mode to see what would happen without touching files
- Custom rules via a simple JSON config file
- Undo support for the most recent organize operation
- Safe error handling with auto-renaming instead of overwrites
- Recursive mode with generated-folder protection
- Date partitioning into paths like `Images/2026-06/photo.jpg`
- Quarantine mode for unknown extensions
- JSONL audit logs for moves, dry runs, and errors
- Max-file guardrails for safer large-folder runs

## Installation

```bash
cd file-organiser
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or install as a package in editable mode:

```bash
pip install -e .
file-organizer --version
```

## Usage

Run the tool through `main.py`:

```bash
python main.py <command> <folder> [options]
```

Preview what would happen:

```bash
python main.py preview ~/Downloads
```

Organize a folder:

```bash
python main.py organize ~/Downloads
```

Dry run:

```bash
python main.py organize ~/Downloads --dry-run
```

Recursive scan:

```bash
python main.py preview ~/Downloads --recursive
```

Partition by modified month:

```bash
python main.py organize ~/Downloads --partition-by-date
```

Quarantine unknown extensions:

```bash
python main.py organize ~/Downloads --quarantine-unknown
```

Write a JSONL audit log:

```bash
python main.py organize ~/Downloads --json-log ~/Downloads/.organizer-events.jsonl
```

Limit a large run:

```bash
python main.py organize ~/Downloads --max-files 250
```

Undo the last organize:

```bash
python main.py undo ~/Downloads
```

Use a custom rules file:

```bash
python main.py organize ~/Downloads --config rules.example.json
```

## Custom Rules Format

A rules file is a JSON object mapping category names to lists of file extensions:

```json
{
  "Images": [".jpg", ".png", ".gif"],
  "Documents": [".pdf", ".docx", ".txt"],
  "Code": [".py", ".js", ".ts"]
}
```

Extensions are case-insensitive, and the leading dot is optional. Anything that does not match a rule goes into `Other/`, or `Quarantine/` when `--quarantine-unknown` is enabled.

## Project Structure

```text
file-organiser/
|-- main.py
|-- pyproject.toml
|-- requirements.txt
|-- README.md
|-- CHANGELOG.md
|-- rules.example.json
|-- tests/
`-- file_organizer/
    |-- __init__.py
    |-- cli.py
    |-- organizer.py
    |-- rules.py
    `-- history.py
```

## How Undo Works

When you run `organize`, a hidden file called `.organizer_history.json` is written to the target folder. It records every `(new_location, original_location)` pair plus run metadata. Running `undo` reads that file, moves everything back, removes empty generated folders, and deletes the history file.

## Tests

```bash
pytest
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT.
