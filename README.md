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

- Not live open-web finding in production. The default people source is unconnected. Owner-deposited JSONL is a stub, not the product.
- Not outreach. Messaging is cycle 2.
- Not a sales conversation, booking calendar, quote engine, or embeddable chat.

## Handoff

Cold person + reason + source + channel. That object does not send a message.
Cycle 2 owns the first write. CRM holds Cold through Done.

When `CRM_BASE_URL` and `INTERNAL_TASK_SECRET` are set, assembled people are
POSTed to CRM Cold. `BusinessSeed.business_id` must be the CRM tenant id, and
the person must already have a phone or email. Failures are swallowed so search
is not blocked.

## Outcome feedback (inbound)

`api.py` (`uvicorn evorove_lead.api:app`) is this repo's only network-facing
code: `POST /api/v1/internal/hypothesis-outcomes`, behind the same
`INTERNAL_TASK_SECRET`. Cycle 2/3 report `hypothesis_id -> outcome`
(`done` / `dropped` / `offer_made` / `in_progress`) when a case closes or
drops -- no name, contact, or message text; the request schema has no field
for one. Feeds the warehouse's `hypothesis_outcomes` table for a future
reweighting job, not `LeadGenerationEngine` itself.

Owner-facing contract (Russian): [`docs/cycle-1-contract.md`](docs/cycle-1-contract.md).
