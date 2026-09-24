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
