# Evorove Lead

Cycle 1 of one product, not a standalone lead factory.

Evorove is three sequential loops in three repositories:

1. **This repo** (`evorove_lead`) — see the business, understand the offer, find a fitting person **with a reason**.
2. **`evorove`** (sister: sale) — write to that person and sell until they are ready to book.
3. **`evorove-crm`** — collect what the service needs and put a specific hour on the calendar.

The product north star lives in the sales sister, not here: `/Users/alenakulish/dev/evorove/FOUNDATION.md`.

## What this repo is

- `LeadGenerationEngine`: public site URL in → grounded offer → people who fit, each with a reason.
- A candidate is an identity we may later address **plus** a grounded reason **plus** the source of that reason. A contact with no reason is dropped. The engine does not stamp the offer onto a phone number to make it “fit.”
- The owner points at **their** site. They do not have to paste ad copy. The engine reads that page. It does not invent price, discount, guarantee, or legal claims.

## What this repo is not

- Not live customer finding in production. People search is a port; no directory is connected here yet (no LinkedIn scrape, no ads OAuth).
- Not outreach. This repo does not email, SMS, or otherwise message the person. Messaging is cycle 2.
- Not a sales conversation, booking calendar, quote engine, or embeddable chat.
- Not a CRM card dump and not a clone of `evorove` or `evorove-crm`.

## Handoff to `evorove`

When the engine keeps a candidate, it prepares for cycle 2:

- who we may address,
- why they belong here,
- where that reason came from,
- a channel cycle 2 may later use to write.

That object does not send. Cycle 2 owns the first message. Cycle 3 owns the hour. There is no integration between the three repos yet.

Owner-facing contract (Russian): [`docs/cycle-1-contract.md`](docs/cycle-1-contract.md).
