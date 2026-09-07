# Evorove Lead agent instructions

- Communicate with the product owner in Russian. Any future customer-facing copy remains English.
- Read this repo’s `FOUNDATION.md` first, then the product north star at `/Users/alenakulish/dev/evorove/FOUNDATION.md`. Every action is checked against the three cycles: lead generation → sale → CRM booking. `CLAUDE.md` is only a pointer plus hard operational rules.
- This repository owns **cycle 1 only**. `LeadGenerationEngine` sees the business, understands the offer from that business’s own words, and keeps people **with a grounded reason**. Cycle 2 (sale until ready to book) lives in `evorove`. Cycle 3 (book the hour) lives in `evorove-crm`. Do not mix those goals.
- While the alignment stage is open, follow `/Users/alenakulish/dev/evorove/docs/foundation-alignment-stage-ru.md`. Do not start live people search or a write-queue until cycle 2 can accept a found person. Three-repo map: `/Users/alenakulish/dev/evorove/docs/three-repos-next-steps-ru.md`. Cycle 1 contract: `docs/cycle-1-contract.md`.
- Cycle 1 result is a person plus a reason they belong here. Not a contact dump. Not a filled CRM card. Not a booked slot. Reject records with no reason. Do not append the offer to a phone number to force a match.
- The owner points at their public site. They do not have to paste ad copy. The engine may fetch that site. Do not scrape paywalled or third-party ads. Do not scrape Google Ads Transparency Center internals. Do not scrape LinkedIn. Do not connect Bing/Google/Meta OAuth; the owner enters secrets herself later.
- Do not message the person. Outreach is cycle 2 in `evorove`. Do not run objections, GREET, quotes, booking, or a chat widget here.
- Do not copy `src/`, the widget, Pulse landing, billing, sales playbook, `ProcessState`, `SalesStage`, or Docker from the sister repos. Law is not the product. No industry forks in code.
- Microsoft Ad Library is public but EEA-impression-only (DSA). It is a later optional research input, not the US SMB source of truth.
- AI may analyze the owner’s public page and phrase why a person fits. It may not invent price, discount, guarantee, or a legal claim. It may not book a slot.
- People search is the `PeopleSearch` port. Default is unconnected. Do not pretend the repo already finds customers.
- Never run `git push`; only the owner pushes. Do not create a GitHub remote.
- Do not read, request, print, edit, or create secrets and local `.env` files. Do not log into or create accounts.
- Do not add `SalesStage` or `ProcessState`. Do not implement email/SMS send or a queue of “write to this phone.”
- Do not train a model, approve knowledge cards, or download pirated books.
- Keep changes inside the assigned file scope. Run focused tests for changed behavior. Do not weaken tests to accommodate an implementation.
