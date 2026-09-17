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


def _keywords(text: str) -> frozenset[str]:
    return frozenset(match.casefold() for match in _WORD_RE.findall(text or ""))


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
