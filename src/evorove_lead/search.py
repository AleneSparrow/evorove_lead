"""People search port. Cycle 1 may find; it may not write."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence

from evorove_lead.offer import OfferUnderstanding

if TYPE_CHECKING:
    from evorove_lead.hypothesis import Hypothesis


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


@dataclass(frozen=True)
class TraceFinding:
    """One raw trace a hypothesis's query turned up, plus what became of it.

    Every finding is a trace, full stop -- it goes to the warehouse whether
    or not a person came out of it. `hit` is set only when a person could
    be extracted (address + fact + source); otherwise `reject_reason` says
    why the trace never became a candidate at all.
    """

    url: str
    raw_text: str
    query_used: str
    source_channel: str
    language: str = ""
    geo_hint: str = ""
    hit: PeopleHit | None = None
    reject_reason: str = ""


class HypothesisPeopleSearch(Protocol):
    """Per-hypothesis search port for a real, phase-2 connector.

    Deliberately separate from `PeopleSearch`: that port takes a whole
    offer and returns bare hits. This one takes one hypothesis's query so
    every result stays traceable to the hypothesis (and its warehouse row)
    that produced it, and it returns `TraceFinding` so a trace that never
    became a person is still recorded, not silently dropped.
    """

    connected: bool

    def find(self, hypothesis: "Hypothesis") -> Sequence[TraceFinding]:
        """Return every raw trace for this hypothesis's query. Empty is allowed."""
