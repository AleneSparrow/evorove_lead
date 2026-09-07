# Evorove Lead

Cycle 1 of one product, not a standalone lead factory.

Evorove is three sequential loops in three repositories:

1. **This repo** (`evorove_lead`) — understand the owner’s offer and, later, find a fitting person **with a reason**.
2. **`evorove`** (sister: sale) — write to that person and sell until they are ready to book.
3. **`evorove-crm`** — collect what the service needs and put a specific hour on the calendar.

The product north star lives in the sales sister, not here: `/Users/alenakulish/dev/evorove/FOUNDATION.md`.

## What this repo is

- The home of **lead generation as a cycle**, not as a widget or an intake form.
- A contract: a candidate is an identity we may later address **plus** a grounded reason **plus** the source of that reason. A contact with no reason is rejected.
- A place for the owner to deposit their own business materials (ad copy, their site URL, a service description) so the offer can be understood without inventing price, discount, guarantee, or legal claims.

## What this repo is not

- Not live customer finding. Scaffold and contract only; people search is not implemented.
- Not outreach. This repo does not email, SMS, or otherwise message the person. Messaging is cycle 2.
- Not a sales conversation, booking calendar, quote engine, or embeddable chat.
- Not a CRM card dump and not a clone of `evorove` or `evorove-crm`.

## Handoff to `evorove`

When cycle 1 later produces a candidate, it hands cycle 2:

- who we may address,
- why they belong here,
- where that reason came from,
- a channel cycle 2 may later use to write.

That is still a cold start, not a sale and not a booked slot. Cycle 2 owns the first message. Cycle 3 owns the hour. There is no integration between the three repos yet; do not treat them as already wired.

Owner-facing notes and the full cycle-1 contract (Russian): [`docs/cycle-1-contract.md`](docs/cycle-1-contract.md).
