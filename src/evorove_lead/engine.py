"""Lead generation engine: see the business, understand the offer, keep people with a reason.

Does not message the person, book a slot, or run a sales conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from evorove_lead.business import BusinessSeed
from evorove_lead.candidate import Candidate, CandidateRejected, accept_candidate_for_offer
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.offer import OfferRejected, OfferUnderstanding
from evorove_lead.offer_reader import read_offer
from evorove_lead.policy import LeadGenerationPolicy
from evorove_lead.presence import PresenceRejected, PresenceSource
from evorove_lead.crm_touch import LeadTouchSink, assembled_touch, sink_from_env, stable_person_id, split_identity
from evorove_lead.search import PeopleHit, PeopleSearch, UnconnectedPeopleSearch


class GenerationStatus(str, Enum):
    NEED_PRESENCE = "need_presence"
    OFFER_INCOMPLETE = "offer_incomplete"
    SEARCH_UNCONNECTED = "search_unconnected"
    NO_FIT = "no_fit"
    PEOPLE_FOUND = "people_found"


@dataclass(frozen=True)
class RejectedHit:
    identity: str
    why: str


@dataclass(frozen=True)
class GenerationResult:
    status: GenerationStatus
    offer: OfferUnderstanding | None
    candidates: tuple[Candidate, ...]
    handoffs: tuple[Cycle1Handoff, ...]
    rejected: tuple[RejectedHit, ...]

    @property
    def sent_messages(self) -> tuple[()]:
        """Cycle 1 never sends. Kept so tests can lock the boundary."""

        return ()


class LeadGenerationEngine:
    def __init__(
        self,
        presence: PresenceSource,
        people_search: PeopleSearch | None = None,
        policy: LeadGenerationPolicy | None = None,
        lead_touch_sink: LeadTouchSink | None = None,
    ) -> None:
        self._presence = presence
        self._people_search = people_search or UnconnectedPeopleSearch()
        self._policy = policy or LeadGenerationPolicy()
        self._lead_touch_sink = lead_touch_sink if lead_touch_sink is not None else sink_from_env()

    def generate(self, seed: BusinessSeed) -> GenerationResult:
        empty = GenerationResult(
            status=GenerationStatus.NEED_PRESENCE,
            offer=None,
            candidates=(),
            handoffs=(),
            rejected=(),
        )
        if not self._policy.may_read_presence(seed.site_url):
            return empty
        try:
            materials = self._presence.load(seed)
        except PresenceRejected:
            return empty
        if not materials:
            return empty

        try:
            offer = read_offer(materials)
        except OfferRejected:
            return GenerationResult(
                status=GenerationStatus.OFFER_INCOMPLETE,
                offer=None,
                candidates=(),
                handoffs=(),
                rejected=(),
            )

        if not self._policy.may_seek_people(
            offer=offer, search_connected=self._people_search.connected
        ):
            return GenerationResult(
                status=GenerationStatus.SEARCH_UNCONNECTED,
                offer=offer,
                candidates=(),
                handoffs=(),
                rejected=(),
            )

        accepted: list[Candidate] = []
        handoffs: list[Cycle1Handoff] = []
        rejected: list[RejectedHit] = []
        for hit in self._people_search.find(offer):
            try:
                candidate = _accept_hit(hit, offer)
            except CandidateRejected as exc:
                rejected.append(RejectedHit(identity=hit.identity, why=str(exc)))
                continue
            accepted.append(candidate)
            name, phone, email = split_identity(candidate.identity)
            person_id = (
                stable_person_id(seed.business_id, phone=phone, email=email, identity=candidate.identity)
                if seed.business_id
                else ""
            )
            handoff = Cycle1Handoff.from_candidate(candidate, hit.channel, person_id=person_id)
            handoffs.append(handoff)
            if seed.business_id and (phone or email):
                self._lead_touch_sink.publish(
                    seed.business_id,
                    assembled_touch(seed.business_id, candidate, handoff),
                )

        status = (
            GenerationStatus.PEOPLE_FOUND if accepted else GenerationStatus.NO_FIT
        )
        return GenerationResult(
            status=status,
            offer=offer,
            candidates=tuple(accepted),
            handoffs=tuple(handoffs),
            rejected=tuple(rejected),
        )


def _accept_hit(hit: PeopleHit, offer: OfferUnderstanding) -> Candidate:
    return accept_candidate_for_offer(
        identity=hit.identity,
        reason=hit.observed_fact,
        reason_source=hit.observed_source,
        offer=offer,
    )
