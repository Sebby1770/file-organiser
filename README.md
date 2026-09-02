# File Organiser

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml/badge.svg)](https://github.com/Sebby1770/file-organiser/actions/workflows/ci.yml)

**Know what to delete. Keep what matters.**

File Organiser is a local desktop utility that scans a folder you choose and explains likely cleanup candidates in three buckets:

- **Delete:** known regenerable caches and known junk on the current advice list.
- **Review:** large, old, duplicate, empty, installer, build, or ambiguous items.
- **Keep:** personal libraries, documents, photos, code, mail, keys, roots, and protected paths.

The desktop app does not upload filenames or files. Review and Keep cannot move through its Trash endpoints. Delete rows move only after selection and confirmation, using the operating-system Trash; if Trash fails, nothing is permanently deleted as a fallback.

Product site: [sebby1770.github.io/file-organiser](https://sebby1770.github.io/file-organiser/)

## Current distribution status

The website checks GitHub Releases live. Do not assume a packaged download exists merely because CI built an artifact.

Any current workflow-produced binaries are **unsigned technical-evaluator builds**. Gatekeeper or SmartScreen may warn or block them. Do not disable operating-system security globally. Verify the Release checksum and use the source preview if you are comfortable evaluating it.

The macOS runner asset is not claimed to be universal; supported architecture must be stated and tested per Release. See [SIGNING.md](SIGNING.md) for the trusted-release gate.

## Run the source preview

Python 3.9 or newer is required.

```bash
git clone https://github.com/Sebby1770/file-organiser.git
cd file-organiser
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m file_organiser
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1` and use `python` instead of `python3`.

Do **not** run `pip install file-organizer`: that US-spelled PyPI name belongs to an unrelated project. This project is not advertising a PyPI install until its exact distribution name has been published and verified.

## Desktop workflow

1. Choose Downloads, your home folder, or another folder.
2. Read the reason on every Delete / Review / Keep row.
3. Use the map and inspector to understand space, not as an automatic deletion score.
4. Select only Delete rows you recognise and confirm the OS Trash move.
5. Verify Trash before emptying it. Emptying Trash is a separate and potentially irreversible action.

“Scan my home folder” means readable home-folder content, not a raw whole-disk scan. Most hidden personal directories are skipped; recognised cache directories are included. Permission errors and files changing during a scan can make totals incomplete.

## Product features

- Sunburst and treemap views with folder zoom.
- Explainable Delete / Review / Keep advice.
- Protected-path locks enforced by the local server.
- Per-launch API token, loopback Host enforcement, exact-Origin checks, restrictive browser headers, and JSON-only mutations.
- Duplicate hashing with an explicit keeper and immediate pre-action revalidation.
- Preview-first category organisation with existing history and undo support.
- Explicit local JSON and CSV scan-report export.
- Native folder picker, reveal, path copy, stop, and keyboard shortcuts.
- Power-user CLI for advice, maps, organise previews, duplicates, clean, undo, why, doctor, and watch.

Recommendations are evidence, not guarantees. Keep tested backups of irreplaceable data and start with a small folder you understand.

## CLI for power users

Running with no arguments opens the desktop app:

```bash
python3 -m file_organiser
```

Common explicit commands:

```bash
python3 -m file_organiser advise ~/Downloads
python3 -m file_organiser map ~/Downloads
python3 -m file_organiser preview ~/Downloads
python3 -m file_organiser duplicates ~/Downloads
python3 -m file_organiser doctor
```

Dry-run is the default for destructive CLI workflows. CLI flags can expose actions beyond the guarded desktop interface; read `--help` before applying them.

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
planned, committed, or rolled back; CSV keeps stable move columns. Paths,
categories, symlink components, and stale source/destination fingerprints are
revalidated before anything is changed.

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

## Build and test

```bash
python3 -m pip install -e ".[test]"
python3 -m pytest -q
python3 -m pip install pyinstaller
python3 scripts/build_app.py
```

The tag-triggered `release-app` workflow tests, packages each runner output, creates `SHA256SUMS.txt`, and publishes durable assets to a GitHub Release. Signing and notarisation credentials are intentionally not included.

## Commercial direction

The current source preview remains MIT licensed. The planned Founding Supporter offer is US$19 one time (intended US$29 later), but checkout is not live. Paid value must exist before sale: signed delivery, managed updates, scan history, reminders, and a support response target.

See [COMMERCIAL.md](COMMERCIAL.md) for launch gates, [PUBLISHING.md](PUBLISHING.md) for safe Python publishing preparation, and the public [pricing page](https://sebby1770.github.io/file-organiser/pricing.html) for the customer-facing boundary.

## Privacy and responsible reports

The public site ships with a blank GA4 measurement ID. No Google Analytics request is made until a valid ID is configured and the visitor explicitly opts in. Desktop file data is excluded from website analytics.

Bug reports and early-access requests are public GitHub issues. Never paste filenames, home paths, exported reports, credentials, payment information, or employer data.

## Licence

[MIT](LICENSE) © Sebastian Forbes.

Site and source: [GitHub](https://github.com/Sebby1770/file-organiser). MIT.
