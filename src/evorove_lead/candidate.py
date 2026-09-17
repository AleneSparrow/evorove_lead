"""Candidate invariant for cycle 1.

A record is a candidate only if we have someone we may later address,
a reason they belong here, and the source of that reason.
This module does not find people or contact them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evorove_lead.offer import OfferUnderstanding

_WORD_RE = re.compile(r"[A-Za-z']{4,}")

# Words common to almost any business's own copy, regardless of what it
# actually sells -- "service", "offer", "customer", and the like. Counting
# these as a fit signal means a random unrelated business's page (a tire
# shop's, a transmission shop's) matches *any* offer, because it also
# talks about its "service" or its "customers". Found piloting the first
# live search on evorove.com: its own FAQ copy is built from this exact
# generic business vocabulary, so its `who_may_fit`/`what_we_sell` claims
# picked up "service" -- and that alone made unrelated small-business
# pages from the open web look like a fit. A real fit needs overlap on a
# word specific enough to name what is actually being sold or who it is
# for.
_GENERIC_FIT_WORDS = frozenset(
    {
        "service", "services", "business", "businesses", "company", "companies",
        "product", "products", "sell", "sells", "selling", "offer", "offers",
        "offering", "customer", "customers", "client", "clients",
    }
)


class CandidateRejected(ValueError):
    """A record is not a cycle-1 candidate."""


@dataclass(frozen=True)
class Candidate:
    """A fitting person plus why they belong here.

    `identity` is someone cycle 2 may later address. This object is not a
    send queue, a CRM card, or a booking.
    """

    identity: str
    reason: str
    reason_source: str


def _require(field: str, value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise CandidateRejected(f"{field} is required")
    return text


def accept_candidate(*, identity: str, reason: str, reason_source: str) -> Candidate:
    """Accept a candidate or reject it.

    Product invariant: a contact without a reason is not a candidate.
    """

    return Candidate(
        identity=_require("identity", identity),
        reason=_require("reason", reason),
        reason_source=_require("reason_source", reason_source),
    )


def _keywords(text: str, *, drop_generic: bool = False) -> frozenset[str]:
    words = frozenset(match.casefold() for match in _WORD_RE.findall(text or ""))
    return words - _GENERIC_FIT_WORDS if drop_generic else words


def _is_offer_text_pasted_as_reason(reason: str, claims: tuple) -> bool:
    """Reject the offer's own slogan standing in for a fact about this person.

    A candidate whose "reason" is just a claim from the brief, verbatim, is
    not a concrete fact about *this* person's situation -- it is the
    client's own text pasted next to a contact, the exact dump the contract
    calls out ("дописанный оффер к телефону").
    """

    folded_reason = reason.casefold().strip()
    return any(claim.text.casefold().strip() == folded_reason for claim in claims)


def accept_candidate_for_offer(
    *,
    identity: str,
    reason: str,
    reason_source: str,
    offer: OfferUnderstanding,
) -> Candidate:
    """Accept a candidate whose reason is tied to the understood offer.

    Uses the same fit / evidence re-selection as the engine (selection.py),
    with only the offer's own claims as vocabulary.
    """

    candidate = accept_candidate(
        identity=identity,
        reason=reason,
        reason_source=reason_source,
    )
    from evorove_lead.selection import SelectionProfile, select

    selection = select(candidate.reason, SelectionProfile.from_offer(offer), addressable=True)
    if not selection.accepted:
        raise CandidateRejected(selection.why)
    return candidate
