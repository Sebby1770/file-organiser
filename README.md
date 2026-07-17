# File Organizer

A preview-first command-line tool that sorts files into clear category folders
without sacrificing reversibility. Plans are deterministic, applies are
transactional, duplicate detection is content-based, and every successful
operation has an auditable undo manifest.

The runtime is pure Python standard library: no third-party package is required.

## Why this version is safe to trust

- **Nothing moves by default.** `plan`, `preview`, and plain `organize` only show
  what would happen. A write requires `apply` or the explicit `organize --apply`.
- **Applies are transactions.** Sources are verified immediately before the
  move. If an apply fails, completed moves are rolled back and the outcome is
  recorded rather than silently leaving an unknown state.
- **Public paths are never blindly unlinked.** A move commits through a private,
  journaled same-parent quarantine. Concurrent replacements are restored or
  preserved, and interrupted commits remain visible to rollback and history.
- **Undo is verified.** Manifests use paths relative to the chosen root and
  fingerprints based on byte size and SHA-256. Changed, missing, or conflicting
  files stop an unsafe restore.
- **No silent overwrites.** Destination collisions can be renamed, skipped, or
  treated as errors. A plan reserves names before anything touches the disk.
- **Symlinks are boundaries.** The scanner does not follow symlinks and rejects
  unsafe path components, filesystem roots, and paths escaping the target.
- **Output can be automated.** Every command supports `--json`, with a versioned
  top-level schema and errors written as JSON to standard error.

## Install

Python 3.10 or newer is required.

```bash
git clone https://github.com/Sebby1770/file-organiser.git
cd file-organiser
python -m pip install .
file-organizer --version
```

For local development, install an editable copy:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
```

You can also run `python -m file_organizer` or the compatible
`python main.py` entry point directly from a checkout.

## Quick start

Review a plan first:

```bash
file-organizer plan ~/Downloads
```

Apply a freshly generated plan as a recorded transaction:

```bash
file-organizer apply ~/Downloads
```

Inspect the undo before restoring anything, then apply it:

```bash
file-organizer undo ~/Downloads --dry-run
file-organizer undo ~/Downloads
```

The compatibility workflow is equally safe: `organize` previews unless
`--apply` is present.

```bash
file-organizer organize ~/Downloads
file-organizer organize ~/Downloads --apply
```

## Commands

### `plan` and `preview`

Both commands create the same read-only organization plan.

```bash
file-organizer plan ROOT [OPTIONS]
file-organizer preview ROOT [OPTIONS]
```

Options:

| Option | Meaning |
| --- | --- |
| `-r`, `--recursive` | Include nested files while preserving their relative path inside each category. |
| `-c`, `--config FILE` | Load validated JSON categories and scan defaults. |
| `--collision rename\|skip\|error` | Resolve occupied destination paths; default is `rename`. |
| `--duplicates keep\|skip\|error` | Keep, omit, or reject byte-identical copies; default is `keep`. |
| `--ignore GLOB` | Add an ignore glob. Repeat the option for multiple patterns. |
| `--include-hidden` | Include dotfiles and files under hidden directories. |
| `--json` | Emit the versioned plan document. |

`rename` deterministically chooses names such as `photo (1).jpg`. Recursive mode
does not flatten the source tree: `client/logo.png` becomes
`Images/client/logo.png`.

### `apply` and `organize`

`apply ROOT` generates a new plan and commits it. It accepts all planning
options. `organize ROOT` accepts those same options but remains read-only until
you add `--apply`; `--dry-run` is available to make preview intent explicit.

```bash
file-organizer apply ~/Downloads --recursive --collision error
file-organizer organize ~/Desktop --config rules.example.json --apply
```

Plans are intentionally not accepted from arbitrary JSON files: an apply always
creates a fresh plan from current filesystem state, then rechecks the sources
before moving them.

### `undo`

```bash
file-organizer undo ROOT [--dry-run] [--operation ID] [--json]
```

Without `--operation`, undo selects the newest eligible applied operation.
Use `history` to find an older ID. `--dry-run` returns exit status `1` if the
undo has conflicts, making it useful as a preflight check in scripts.

### `duplicates`

```bash
file-organizer duplicates ROOT --recursive
```

Duplicate discovery groups candidates by size before hashing them with SHA-256.
It reports the potentially reclaimable bytes but never deletes or moves a file.
The command accepts `--recursive`, `--config`, `--ignore`, `--include-hidden`,
and `--json`.

### `history`

```bash
file-organizer history ROOT
file-organizer history ROOT --json
```

History is stored as one retained manifest per operation below
`.file-organizer/history` in the organized root. Journal status is updated with
atomic file replacement as apply or undo advances; completed records are kept
instead of being erased. Do not hand-edit transaction manifests.

## Custom rules

Pass `--config rules.example.json` to replace the built-in category map. The
rich format supports category extensions, the fallback category, ignore globs,
and a hidden-file default:

```json
{
  "categories": {
    "Design": [".fig", ".psd"],
    "Writing": [".docx", ".md", ".pdf"]
  },
  "default_category": "Other",
  "ignore_patterns": ["*.part", "in-progress/**"],
  "include_hidden": false
}
```

Extensions are case-insensitive and a leading dot is optional. Multi-part
extensions are supported, so `.tar.gz` takes precedence over `.gz`. Category
names are validated for Windows and POSIX path safety. The original compact
shape (`{"Images": ["jpg", "png"]}`) remains supported.

Built-in ignores protect `.git`, `.file-organizer`, and Python cache folders.
CLI `--ignore` values are added to config and built-in patterns rather than
replacing them.

## JSON and exit statuses

Successful `--json` responses contain `schema_version`, `kind`, the resolved
root, a summary, and command-specific operations. Field ordering is stable and
paths in plans are relative to the root. Expected failures produce a versioned
`kind: "error"` document on standard error.

| Status | Meaning |
| --- | --- |
| `0` | Command completed, including a clean read-only preview. |
| `1` | Configuration, safety, planning, history, conflict, or transaction error. |
| `2` | Invalid command-line usage. |
| `130` | Interrupted by the user. |

## Development

```bash
python -m compileall -q file_organizer
ruff check .
python -m unittest discover -s tests -v
python -m build
```

CI runs the package and command-line tests across supported Python versions.
See [CHANGELOG.md](CHANGELOG.md) for the v2 redesign.

## License

[MIT](LICENSE)
