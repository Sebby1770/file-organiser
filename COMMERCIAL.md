# Commercial launch plan

File Organiser is currently an MIT-licensed source preview. Existing MIT releases cannot be retroactively made proprietary, and shipping all paid value as public MIT code makes licence enforcement weak. The commercial offer should therefore earn money through trusted delivery, convenience, continued updates, and support—not by weakening free safety rails.

## Positioning

**Promise:** know what to delete; keep what matters.

**Initial customer:** a non-technical Mac or Windows user with a crowded Downloads/home folder who wants understandable, local cleanup advice and is uncomfortable with opaque “one-click cleaner” software.

**Do not claim:** whole-disk coverage, guaranteed-safe recommendations, automatic recovery, signed builds, universal Mac support, verified customer outcomes, or a support SLA until each is proven.

## Founding offer to validate

- Community preview: free source and explicitly labelled unsigned evaluator assets.
- Founding Supporter: planned **US$19 one time**, with an intended later individual price of **US$29**.
- Include only when working: signed/notarised delivery, managed updates, scan history, reminders, and one year of feature updates.
- Intended paid-support target: first response within two business days. This is not a resolution or uptime SLA.
- Business support: defer pricing and sales until signed deployment, support capacity, licence terms, and admin requirements are real.

Checkout is deliberately absent. Early access uses a public GitHub issue template and must warn people not to submit sensitive file or payment data.

## Launch gates

1. Publish at least one durable GitHub Release with checksums and accurate OS/architecture notes.
2. Complete signing/notarisation and clean-machine launch tests for every OS sold as supported.
3. Ship the promised paid product value; do not sell roadmap bullets.
4. Create licence terms, refund policy, privacy update, tax handling, payment-processor disclosure, and support workflow.
5. Recruit consented pilots and publish limitations with every case study.
6. Validate the US$19 price with early-access conversion before building business tiers.

## Metrics worth collecting

Only public-site events after valid GA4 configuration and explicit opt-in: install-options view, compatible-release CTA, pricing view, and early-access continuation. Never send local paths, filenames, hashes, scan totals, advice, cleanup choices, or desktop events.

Product evidence should be collected deliberately and with consent: time to first confident action, accepted versus rejected recommendations, false positives, prevented protected-path actions, and bytes actually freed after separately emptying Trash.

## Licence and publishing decisions

- Keep current core under MIT unless counsel and the maintainer intentionally choose an open-core boundary for new modules.
- Do not add a payment service or licence secret to the public repository.
- Reserve the exact PyPI name before advertising a PyPI install. The US-spelled `file-organizer` name belongs to an unrelated project.
- Prefer GitHub Trusted Publishing for a future Python release; see `PUBLISHING.md`.
