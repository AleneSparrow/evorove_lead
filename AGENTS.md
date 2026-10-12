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
- People search is the `PeopleSearch` port. Default is `UnconnectedPeopleSearch`. When `WEB_SEARCH_BASE_URL` is set, `WebSearchPeopleSearch` may run; Cold still requires contract re-analysis. Client 0 (`python -m evorove_lead.client_zero`) is the first live metric. Owner-deposited JSONL is a stub. Do not pretend search already finds customers for arbitrary tenants.
- Never run `git push`; only the owner pushes. Do not create a GitHub remote.
- Do not read, request, print, edit, or create secrets and local `.env` files. Do not log into or create accounts.
- Do not add `SalesStage` or `ProcessState`. Do not implement email/SMS send or a queue of “write to this phone.”
- Do not train a model, approve knowledge cards, or download pirated books.
- Keep changes inside the assigned file scope. Run focused tests for changed behavior. Do not weaken tests to accommodate an implementation.

## Cursor Cloud specific instructions

- Install with `python3 -m pip install --user -e ".[dev]"`, then `python3 -m playwright install --with-deps chromium`. Login shells do not load `~/.local/bin`; symlink `pytest`, `uvicorn`, `alembic`, and `playwright` into `/usr/local/bin`.
- The render test in `tests/test_rendering.py` needs that Chromium binary. Without it the import succeeds and the test fails.
- Local Postgres for this repo is not the CRM database. Without Docker, start it with `sudo pg_ctlcluster 16 main start`. The role and database are both `evorove_lead`. Use port 5432. `docker compose` still publishes 5435.
- `python3 -m pytest` is the suite. `GET /health` on `uvicorn evorove_lead.api:app --host 0.0.0.0 --port 8002` does not need `INTERNAL_TASK_SECRET`.
- Do not put `ANTHROPIC_API_KEY` into the agent environment. An unknown `ANTHROPIC_MODEL` price blocks a paid classification instead of treating the price as zero.

