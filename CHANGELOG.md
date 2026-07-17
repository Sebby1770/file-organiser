# Changelog

All notable changes to this project are documented here.

## 2.0.0 - 2026-07-17

### Added

- Preview-first `plan`, `preview`, and `organize` workflows plus a first-class
  explicit `apply` command.
- Transaction manifests with source fingerprints, rollback reporting, operation
  IDs, durable history, dry-run undo, and targeted `undo --operation` support.
- Journaled same-parent quarantine commits that preserve concurrent pathname
  replacements and make post-mutation interrupts rollback-accountable.
- Recursive organization, configurable ignore globs, hidden-file control, and
  deterministic collision strategies.
- Size-prefiltered SHA-256 duplicate discovery and configurable duplicate policy.
- Versioned JSON output for plans, applies, undo, duplicate reports, history,
  expected errors, and command-line usage errors.
- Validated rich JSON rules with multi-part extensions and legacy config support.
- Python packaging, console entry point, standard-library-only runtime, and CI.

### Changed

- Organization never writes by default. Use `apply` or `organize --apply` to
  move files.
- Destination selection reserves every planned path before execution and never
  silently overwrites existing content.
- Filesystem traversal is deterministic, does not follow symlinks, and protects
  managed directories from being reorganized.

### Removed

- The Rich runtime dependency and the single mutable history-file design.
- Duplicate legacy modules at the repository root.

## 1.0.0 - 2026-05-05

- Initial organizer with preview, organize, custom rules, and single-step undo.
