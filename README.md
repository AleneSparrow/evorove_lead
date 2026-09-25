# Evorove Lead

Cycle 1 of one product, not a standalone lead factory.

Evorove is three sequential loops in three repositories. The owner’s evening screen is a **four-tab CRM** (Cold, In progress, Offer made, Done):

1. **This repo** (`evorove_lead`) — analyze the client (their site is a **brief**, not a lead list), search the **open web**, re-analyze, put a fitting person **with a reason** on **Cold**.
2. **`evorove`** — write cold and sell (In progress / Offer made).
3. **`evorove-crm`** — the board plus close: sale completed or an appointment if the service is offline (**Done**).

The product north star lives in the sales sister, not here: `/Users/alenakulish/dev/evorove/FOUNDATION.md`.

## What this repo is

- `LeadGenerationEngine` should: understand who to look for → parse open-web data → keep people who fit, each with a reason → hand them to CRM Cold.
- A candidate is an identity we may later address **plus** a grounded reason **plus** the source of that reason. A contact with no reason is dropped.
- The owner’s site is input to the **brief**. It is not where leads come from.

## What this repo is not

- Not live open-web finding for arbitrary tenants in production. The default people source is `UnconnectedPeopleSearch`; when the owner sets `WEB_SEARCH_BASE_URL`, `WebSearchPeopleSearch` runs and Cold can fill after re-analysis. Client 0 (Evorove selling itself) is the first live metric, not a promise that search already finds customers for any business.
- Not outreach. Messaging is cycle 2.
- Not a sales conversation, booking calendar, quote engine, or embeddable chat.

## Handoff

Cold person + reason + source + channel. That object does not send a message.
Cycle 2 owns the first write. CRM holds Cold through Done.

When `CRM_BASE_URL` and `INTERNAL_TASK_SECRET` are set, assembled people are
POSTed to CRM Cold. `BusinessSeed.business_id` must be the CRM tenant id, and
the person must already have a phone or email. A failed POST never blocks
search: it is logged (business_id, touch_id, error -- never a contact) and
queued in the `crm_deliveries` outbox (migration `0006`), and
`redeliver_pending` retries it on the next run/flush. Redelivery is safe --
CRM dedupes by `touch_id`, so an accepted duplicate comes back as
`duplicate=True`. `crm_pending`/`crm_redelivered` counts show up in
`client_zero`, `run`, and the internal API
(`GET/POST /api/v1/internal/crm-deliveries/status|flush`).

## Outcome feedback (inbound)

`api.py` (`uvicorn evorove_lead.api:app`) is this repo's only network-facing
code: `POST /api/v1/internal/hypothesis-outcomes`, behind the same
`INTERNAL_TASK_SECRET`. Cycle 2/3 report `hypothesis_id -> outcome`
(`done` / `dropped` / `offer_made` / `in_progress`) when a case closes or
drops -- no name, contact, or message text; the request schema has no field
for one. Feeds the warehouse's `hypothesis_outcomes` table for a future
reweighting job, not `LeadGenerationEngine` itself.

## Search trigger (step 20)

The owner pastes her site on the CRM board and presses **Find people**. The
CRM calls this service; nobody runs the CLI by hand.

- `POST /api/v1/internal/searches` `{business_id, site_url}` -- remembers the
  site for this business and runs cycle 1 in the background (open web through
  `WEB_SEARCH_BASE_URL`, geo read from the brief). 503 when search is not
  connected, 422 for a private/invalid URL.
- `GET /api/v1/internal/searches/{business_id}` -- last status and how many
  people went to Cold.
- `POST /api/v1/internal/searches/run-due` -- the daily cron: re-runs every
  remembered site not run in the last 20 hours. People already on the board
  are not sent again.

All three need `X-Internal-Task-Secret` (same `INTERNAL_TASK_SECRET` as the CRM
and cycle 2).

Deploy: one web service from this repo (the Dockerfile runs migrations and
`uvicorn evorove_lead.api:app`) with `DATABASE_URL`, `INTERNAL_TASK_SECRET`,
`CRM_BASE_URL` and `WEB_SEARCH_BASE_URL`; a SearxNG service with the JSON
format enabled for `WEB_SEARCH_BASE_URL` (build `searxng/Dockerfile`, keep it
private); a daily cron -- either POST `run-due`, or a cron service from this
repo with start command `python -m evorove_lead.searches`. In the CRM set `EVOROVE_LEAD_BASE_URL` to this service's origin.

## Client 0: running the search

`python -m evorove_lead.client_zero` builds the brief from `https://evorove.com`
(client 0 is Evorove selling itself, not a placeholder salon), runs the
pilot the same way any tenant would, and prints counts only -- status,
candidates, handoffs, rejected, `messages_sent=0`. It never prints an
email, phone, or reason. Exits `2` when search is unconnected (no fake
candidates printed as if a search ran). With `CRM_BASE_URL`,
`INTERNAL_TASK_SECRET`, and `EVOROVE_CLIENT_ZERO_BUSINESS_ID` set, accepted
people also POST to CRM Cold through the same path any tenant uses;
without them the search still runs and only skips that POST.

## Local setup

- `pip install -e .[dev]`, then `python -m playwright install chromium` --
  `presence.py` falls back to a headless render (`rendering.py`) when a
  business's site is a client-rendered SPA (an empty `<div id="root">`
  shell over plain HTTP; `evorove.com` itself is one) and the browser
  binary has to be fetched separately from the Python package.
- `docker compose up -d` starts this repo's own Postgres (5435) and a
  self-hosted SearxNG (8080, phase 2's `WEB_SEARCH_BASE_URL`) -- see
  `.env.example`.

Owner-facing contract (Russian): [`docs/cycle-1-contract.md`](docs/cycle-1-contract.md).
