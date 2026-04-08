# 📁 Smart File Organizer CLI

A clean, beginner-friendly Python command-line tool that automatically sorts files in a folder by type — images, documents, videos, code, and more. Built with `argparse` and [Rich](https://github.com/Textualize/rich) for colorful output and progress bars.

## ✨ Features

- **Three commands**: `organize`, `preview`, `undo`
- **Colored terminal output** with tables and progress bars (via Rich)
- **Dry-run mode** to see what would happen without touching files
- **Custom rules** via a simple JSON config file
- **Undo support** — revert the last organize operation in a folder
- **Safe error handling** — skips missing files, avoids overwrites by auto-renaming
- **Modular structure** — easy to read, easy to extend

## 📦 Installation

```bash
# 1. Clone or download the project
cd file_organizer

# 2. (Recommended) Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

## 🚀 Usage

Run the tool through `main.py`:

```bash
python main.py <command> <folder> [options]
```

### Preview what would happen

```bash
python main.py preview ~/Downloads
```

Shows a table of categories and the files that would go into each. Nothing is moved.

### Organize a folder

```bash
python main.py organize ~/Downloads
```

Moves files into subfolders like `Images/`, `Documents/`, `Videos/`, etc.

### Dry run (simulate organize)

```bash
python main.py organize ~/Downloads --dry-run
```

Logs each move to the terminal but doesn't actually touch any files.

### Undo the last organize

```bash
python main.py undo ~/Downloads
```

Restores every file to its original location and cleans up empty category folders.

### Use a custom rules file

```bash
python main.py organize ~/Downloads --config rules.example.json
```

## 🛠 Custom Rules Format

A rules file is a JSON object mapping category names to lists of file extensions:

```json
{
  "Images": [".jpg", ".png", ".gif"],
  "Documents": [".pdf", ".docx", ".txt"],
  "Code": [".py", ".js", ".ts"]
}
```

Extensions are case-insensitive, and the leading dot is optional. Anything that doesn't match a rule goes into an `Other/` folder.

See `rules.example.json` for a working example.

## 📂 Project Structure

```
file_organizer/
├── main.py                   # Entry point
├── requirements.txt
├── README.md
├── .gitignore
├── rules.example.json        # Example custom rules
└── file_organizer/           # Package
    ├── __init__.py
    ├── cli.py                # argparse CLI
    ├── organizer.py          # Core organize/preview/undo logic
    ├── rules.py              # Default categories + config loader
    └── history.py            # Undo history manager
```

## 🧠 How Undo Works

When you run `organize`, a hidden file called `.organizer_history.json` is written to the target folder. It records every `(new_location, original_location)` pair. Running `undo` reads that file, moves everything back, and deletes the history file. Only the **most recent** organize operation is remembered.

## ⚠️ Notes & Safety

- The organizer only looks at the **top level** of the folder — it doesn't recurse into subdirectories.
- Hidden files (starting with `.`) are skipped.
- If a destination filename already exists, the new file is renamed with a numeric suffix (e.g. `photo (1).jpg`) — nothing is ever overwritten.
- Errors on individual files don't stop the whole run; they're collected and reported at the end.

## 📝 License

MIT — do whatever you want with it.
