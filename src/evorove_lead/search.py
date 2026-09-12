"""People search port. Cycle 1 may find; it may not write."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from evorove_lead.offer import OfferUnderstanding


@dataclass(frozen=True)
class PeopleHit:
    """An untrusted search hit. The engine still requires a grounded reason."""

    identity: str
    observed_fact: str
    observed_source: str
    channel: str = ""


class PeopleSearch(Protocol):
    connected: bool

    def find(self, offer: OfferUnderstanding) -> Sequence[PeopleHit]:
        """Return people who might fit. Empty is allowed. Sending is not."""


class UnconnectedPeopleSearch:
    """Default source: the engine understands the offer and stops.

    Live directories, ads OAuth, and LinkedIn scraping are not wired here.
    """

    connected = False

    def find(self, offer: OfferUnderstanding) -> Sequence[PeopleHit]:
        raise RuntimeError("people search is not connected")


# Connected implementation lives in observations.py: OwnerObservationPeopleSearch
# reads owner-deposited JSONL. It is not a live web directory.
