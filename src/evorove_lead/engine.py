"""Lead generation engine: see the business, understand the offer, keep people with a reason.

Does not message the person, book a slot, or run a sales conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
from evorove_lead.sqlalchemy_warehouse import warehouse_from_env
from evorove_lead.warehouse import (
    AnalysisWarehouse,
    BriefRecord,
    CandidateRecord,
    HypothesisRecord,
    TraceRecord,
    new_id,
)


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
        warehouse: AnalysisWarehouse | None = None,
    ) -> None:
        self._presence = presence
        self._people_search = people_search or UnconnectedPeopleSearch()
        self._policy = policy or LeadGenerationPolicy()
        self._lead_touch_sink = lead_touch_sink if lead_touch_sink is not None else sink_from_env()
        self._warehouse = warehouse if warehouse is not None else warehouse_from_env()

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

        hypothesis_id = self._record_brief_and_hypothesis(seed, offer)

        accepted: list[Candidate] = []
        handoffs: list[Cycle1Handoff] = []
        rejected: list[RejectedHit] = []
        for hit in self._people_search.find(offer):
            if hypothesis_id:
                self._warehouse.save_trace(_hit_to_trace_record(seed.business_id, hypothesis_id, hit))
            try:
                candidate = _accept_hit(hit, offer)
            except CandidateRejected as exc:
                rejected.append(RejectedHit(identity=hit.identity, why=str(exc)))
                if hypothesis_id:
                    self._warehouse.save_candidate(
                        _rejected_hit_to_candidate_record(
                            seed.business_id, hypothesis_id, hit, str(exc)
                        )
                    )
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
            if hypothesis_id:
                self._warehouse.save_candidate(
                    _candidate_to_record(seed.business_id, hypothesis_id, candidate, email=email, phone=phone)
                )
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

    def _record_brief_and_hypothesis(self, seed: BusinessSeed, offer: OfferUnderstanding) -> str:
        """Snapshot the brief and open one bridging hypothesis for this run.

        Phase 1 (`hypothesis.py`, not built yet) replaces this single
        "unstructured" hypothesis with a `HypothesisSet` built around real
        audience segments and intent triggers. Until then, every trace and
        candidate from one generation run is attributed to this one row so
        the warehouse schema (and the tenant scoping on it) is exercised
        end to end without inventing product behavior ahead of its phase.
        """

        if not seed.business_id:
            return ""
        now = datetime.now(timezone.utc)
        brief_id = new_id("brief")
        self._warehouse.save_brief(
            BriefRecord(
                id=brief_id,
                business_id=seed.business_id,
                what_we_sell=tuple(
                    {"text": claim.text, "source_name": claim.source_name}
                    for claim in offer.what_we_sell
                ),
                who_may_fit=tuple(
                    {"text": claim.text, "source_name": claim.source_name}
                    for claim in offer.who_may_fit
                ),
                commercial_claims=tuple(
                    {"kind": claim.kind, "text": claim.text, "source_name": claim.source_name}
                    for claim in offer.commercial_claims
                ),
                must_not_promise=offer.must_not_promise,
                created_at=now,
            )
        )
        hypothesis_id = new_id("hypothesis")
        self._warehouse.save_hypothesis(
            HypothesisRecord(
                id=hypothesis_id,
                business_id=seed.business_id,
                brief_id=brief_id,
                audience_segment="unstructured",
                channel="mixed",
                query_template="",
                intent_trigger="demographic_fit",
                status="live",
                fit_score=0.0,
                evidence_score=0.0,
                reach_estimate=0,
                created_at=now,
            )
        )
        return hypothesis_id


def _accept_hit(hit: PeopleHit, offer: OfferUnderstanding) -> Candidate:
    return accept_candidate_for_offer(
        identity=hit.identity,
        reason=hit.observed_fact,
        reason_source=hit.observed_source,
        offer=offer,
    )


def _hit_to_trace_record(business_id: str, hypothesis_id: str, hit: PeopleHit) -> TraceRecord:
    return TraceRecord(
        id=new_id("trace"),
        business_id=business_id,
        hypothesis_id=hypothesis_id,
        url=hit.observed_source,
        fetched_at=datetime.now(timezone.utc),
        raw_text=hit.observed_fact,
        query_used="",
        source_channel=hit.channel or "unknown",
    )


def _candidate_to_record(
    business_id: str,
    hypothesis_id: str,
    candidate: Candidate,
    *,
    email: str | None,
    phone: str | None,
) -> CandidateRecord:
    return CandidateRecord(
        id=new_id("candidate"),
        business_id=business_id,
        hypothesis_id=hypothesis_id,
        identity=candidate.identity,
        channel="email" if email else "sms" if phone else "unknown",
        reason=candidate.reason,
        reason_source=candidate.reason_source,
        fit=1.0,
        evidence=1.0,
        addressable=bool(email or phone),
        decision="cold",
        decided_at=datetime.now(timezone.utc),
        email=email or "",
        phone=phone or "",
    )


def _rejected_hit_to_candidate_record(
    business_id: str, hypothesis_id: str, hit: PeopleHit, why: str
) -> CandidateRecord:
    return CandidateRecord(
        id=new_id("candidate"),
        business_id=business_id,
        hypothesis_id=hypothesis_id,
        identity=hit.identity,
        channel=hit.channel or "unknown",
        reason=hit.observed_fact,
        reason_source=hit.observed_source,
        fit=0.0,
        evidence=0.0,
        addressable=False,
        decision="rejected",
        decided_at=datetime.now(timezone.utc),
    )
