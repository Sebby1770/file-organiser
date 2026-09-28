# Changelog

## 4.4.0 — tidy preview

### Dry-run tidy

- `file-organiser audit --preview` and the desktop Clutter tab list where loose, categorised files would move.
- The preview uses the same root-only plan as organise and does not create folders or move files.
- Loose files older than 180 days lower the clutter score by up to 10 points.

## 4.3.0 — clutter audit

### Read-only audit

- Added `file-organiser audit` and a desktop Clutter tab.
- Scores loose files in the folder root, copy-like names (`Report (1)`, `Report copy`, `Report final`), empty files, and how many files fall through to Other.
- Reports extensions that the active rules assign to more than one category. The first category still wins; the audit only makes the clash visible.
- The walk skips hidden names, cache directories, and symlinks. It does not move or delete anything.
- The desktop route audits only the folder already scanned in that session.

## 4.2.0 — verified desktop transactions

### Desktop organiser

- Bound preview and apply to one server-held, single-use plan id instead of
  rescanning and generating different moves after confirmation.
- Enabled SHA-256 source fingerprints for desktop plans and preserved hashes
  in undo history so same-size content replacements fail closed.
- Added Standard, Downloads, and Minimal layout selection to the desktop app.
- Added transaction status and exact-id undo controls to the desktop app,
  including preflight validation and clear disabled/error states.
- Blocked scans and overlapping organise operations while a filesystem
  transaction is active.
- Added end-to-end local HTTP tests covering session authorization, stale-plan
  rejection, exact apply, content-drift detection, and safe undo.

## 4.1.0 — commercial-launch candidate

### Desktop product

- Made all desktop Trash operations fail closed; no permanent-delete fallback.
- Enforced Delete-list and protected-path policy across destructive routes.
- Added duplicate-group state, explicit keeper validation, immediate re-hashing, and policy re-checks.
- Added per-launch API authentication, loopback Host enforcement, exact Origin checks, JSON-only mutation bodies, size limits, and browser security headers.
- Escaped filesystem-derived strings in the interface to prevent crafted filenames from becoming markup.
- Added explicit local JSON and CSV scan report exports.
- Corrected default scan wording from “this computer” to “home folder.”
- Moved empty files and installers to Review rather than treating age or zero bytes as universal proof of safe deletion.

### Launch and website

- Rebuilt the multi-page product site with release-aware CTAs, pricing, safety, guide, privacy, about, evidence template, thank-you flow, custom 404, breadcrumbs, internal links, FAQ schema, software schema, sitemap, robots file, and mobile CTA.
- Added unique page titles/descriptions plus absolute Open Graph and X image metadata.
- Added consent-gated GA4 wiring with analytics disabled until a valid measurement ID is configured.
- Added a real solo-maker section and explicitly declined to invent testimonials, team members, a local address, or customer results.
- Upgraded tag releases from expiring Actions artifacts to durable GitHub Releases with SHA-256 manifests.
- Added signing, publishing, and commercial launch gates.
