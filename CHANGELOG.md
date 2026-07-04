# Changelog

## 2.2.0 - 2026-07-04

- Added a `manifest` command that emits JSON inventory data with planned targets, categories, sizes, mtimes, and optional checksums.
- Added `--min-age-seconds` to avoid moving files that may still be downloading or actively written.
- Added tests for manifest generation and recent-file safety skipping.

## 2.1.0 - 2026-07-01

- Added an embedded SQLite run log with a new `history` command.
- Added throughput reporting for organize and dry-run operations.
- Added checksum-based duplicate detection with `--dedupe`, routing repeat content into `Duplicates/`.
- Added run-log database options and tests for duplicate routing and historical run queries.
- Added a Dockerfile and CI container build for repeatable CLI execution.

## 2.0.0 - 2026-06-30

- Fixed the package layout so `python main.py ...` and the `file-organizer` console script both resolve the real package.
- Added recursive scanning with generated-folder protection.
- Added date partitioning, e.g. `Images/2026-06/file.jpg`.
- Added unknown-file quarantine mode.
- Added JSONL audit logging for moved files, dry-run events, and errors.
- Added max-file safety limits, richer undo metadata, recursive empty-folder cleanup, tests, packaging metadata, and CI.

## 1.0.0 - 2026-05-06

- Initial smart file organizer CLI with preview, organize, dry-run, custom rules, undo history, and Rich output.
