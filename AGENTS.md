# Evorove Lead agent instructions

- Communicate with the product owner in Russian. Any future customer-facing copy remains English.
- Read this repo’s `FOUNDATION.md` first, then the product north star at `/Users/alenakulish/dev/evorove/FOUNDATION.md` (revised 12 September 2026). Every action is checked against the three cycles and the four CRM tabs: Cold → In progress → Offer made → Done. `CLAUDE.md` is only a pointer plus hard operational rules.
- This repository owns **cycle 1 only**. Analyze the client (their site/ads are a **search brief**, not a lead list), search the open web, re-analyze, and place people **with a grounded reason** on the CRM **Cold** tab. Cycle 2 (cold write and sale) lives in `evorove`. The CRM board and close (sale or appointment) live in `evorove-crm`. Do not mix those goals.
- The alignment-stage file is historical and **does not override** the north star. Do not scrape LinkedIn or Google Ads Transparency internals. Do not send mail/SMS from this repo. Microsoft Ad Library is EEA-impression-only: optional brief input, not the US people source. Three-repo map: `/Users/alenakulish/dev/evorove/docs/three-repos-next-steps-ru.md`. Cycle 1 contract: `docs/cycle-1-contract.md`.
- Cycle 1 result is a cold person plus a reason they belong here, stored as CRM Cold. Not a contact dump. Not a booked slot. Reject records with no reason. Do not append the offer to a phone number to force a match. Do not treat site visitors as generated leads.
- The owner points at their public site so the engine can form the brief. They do not have to paste ad copy. Do not scrape paywalled or third-party ads. Do not connect Bing/Google/Meta OAuth; the owner enters secrets herself later.
- Do not message the person. Outreach is cycle 2 in `evorove`. Do not run objections, GREET, quotes, booking, or a chat widget here.
- Do not copy `src/`, the widget, Pulse landing, billing, sales playbook, `ProcessState`, `SalesStage`, or Docker from the sister repos. Law is not the product. No industry forks in code.
- Microsoft Ad Library is EEA-only (DSA): optional brief input, not a US people directory.
- AI may analyze the brief and open-web traces and phrase why a person fits. It may not invent price, discount, guarantee, or a legal claim. It may not book a slot.
- People search is the `PeopleSearch` port. Default is `UnconnectedPeopleSearch`. Owner-deposited JSONL is a stub, not the product source. Do not pretend the open web already finds customers.
- Never run `git push`; only the owner pushes. Do not create a GitHub remote.
- Do not read, request, print, edit, or create secrets and local `.env` files. Do not log into or create accounts.
- Do not add `SalesStage` or `ProcessState`. Do not implement email/SMS send or a queue of “write to this phone.”
- Do not train a model, approve knowledge cards, or download pirated books.
- Keep changes inside the assigned file scope. Run focused tests for changed behavior. Do not weaken tests to accommodate an implementation.
