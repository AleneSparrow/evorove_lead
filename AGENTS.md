# Evorove Lead agent instructions

- Communicate with the product owner in Russian. Any future customer-facing copy remains English.
- Read this repo’s `FOUNDATION.md` first, then the product north star at `/Users/alenakulish/dev/evorove/FOUNDATION.md`. Every action is checked against the three cycles: lead generation → sale → CRM booking. `CLAUDE.md` is only a pointer plus hard operational rules.
- This repository owns **cycle 1 only**: understand the offer from owner-deposited materials, and later find a fitting person **with a grounded reason**. Cycle 2 (sale until ready to book) lives in `evorove`. Cycle 3 (book the hour) lives in `evorove-crm`. Do not mix those goals.
- While the alignment stage is open, follow `/Users/alenakulish/dev/evorove/docs/foundation-alignment-stage-ru.md`. Scaffold and contract are allowed in parallel. Do not start live people search or a write-queue until cycle 2 can accept a found person. Three-repo map: `/Users/alenakulish/dev/evorove/docs/three-repos-next-steps-ru.md`. Cycle 1 contract: `docs/cycle-1-contract.md`.
- Cycle 1 result is a person plus a reason they belong here. Not a contact dump. Not a filled CRM card. Not a booked slot. Reject records with no reason.
- Do not message the person. Outreach is cycle 2 in `evorove`. Do not run objections, GREET, quotes, booking, or a chat widget here.
- Do not copy `src/`, the widget, Pulse landing, billing, sales playbook, `ProcessState`, `SalesStage`, or Docker from the sister repos. CRM was copied from the engine once and inherited the wrong product. Do not repeat that. Law is not the product. No industry forks in code.
- Owner deposits business materials (ad copy, their site URL, service description) into `owner-materials/`. Do not scrape paywalled or third-party ads. Do not scrape Google Ads Transparency Center internals. Do not connect Bing/Google/Meta OAuth in this slice; the owner enters secrets herself later.
- Microsoft Ad Library is public but EEA-impression-only (DSA). It is a later optional research input, not the US SMB source of truth, and not this slice.
- AI may later analyze owner materials and phrase why a person fits. It may not invent price, discount, guarantee, or a legal claim. It may not book a slot.
- Never run `git push`; only the owner pushes. Do not create a GitHub remote.
- Do not read, request, print, edit, or create secrets and local `.env` files. Do not log into or create accounts.
- Do not add `SalesStage` or `ProcessState`. Do not implement people search, enrichment, email/SMS send, LinkedIn scraping, or a queue of “write to this phone” in this slice.
- Do not train a model, approve knowledge cards, or download pirated books.
- Do not promise in README or copy that this repo already finds customers.
- Keep changes inside the assigned file scope. Run focused tests for changed behavior. Do not weaken tests to accommodate an implementation.
