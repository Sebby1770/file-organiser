# Changelog

## 2.0.0 - 2026-06-30

- Fixed the package layout so `python main.py ...` and the `file-organizer` console script both resolve the real package.
- Added recursive scanning with generated-folder protection.
- Added date partitioning, e.g. `Images/2026-06/file.jpg`.
- Added unknown-file quarantine mode.
- Added JSONL audit logging for moved files, dry-run events, and errors.
- Added max-file safety limits, richer undo metadata, recursive empty-folder cleanup, tests, packaging metadata, and CI.

## 1.0.0 - 2026-05-06

- Initial smart file organizer CLI with preview, organize, dry-run, custom rules, undo history, and Rich output.
