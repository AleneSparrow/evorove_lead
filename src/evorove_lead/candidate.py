"""Candidate invariant for cycle 1.

A record is a candidate only if we have someone we may later address,
a reason they belong here, and the source of that reason.
This module does not find people or contact them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evorove_lead.offer import OfferUnderstanding


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


def accept_candidate_for_offer(
    *,
    identity: str,
    reason: str,
    reason_source: str,
    offer: OfferUnderstanding,
) -> Candidate:
    """Accept a candidate whose reason is tied to the understood offer."""

    candidate = accept_candidate(
        identity=identity,
        reason=reason,
        reason_source=reason_source,
    )
    folded_reason = candidate.reason.casefold()
    claims = (*offer.what_we_sell, *offer.who_may_fit)
    if not any(claim.text.casefold() in folded_reason for claim in claims):
        raise CandidateRejected("reason is not tied to the offer")
    return candidate
