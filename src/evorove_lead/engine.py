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
from evorove_lead.hypothesis import GeoRadius, Hypothesis, build_hypotheses, verify_hypothesis
from evorove_lead.offer import OfferRejected, OfferUnderstanding
from evorove_lead.offer_reader import read_offer
from evorove_lead.policy import LeadGenerationPolicy
from evorove_lead.presence import PresenceRejected, PresenceSource
from evorove_lead.crm_touch import LeadTouchSink, assembled_touch, sink_from_env, stable_person_id, split_identity
from evorove_lead.search import (
    HypothesisPeopleSearch,
    PeopleHit,
    PeopleSearch,
    TraceFinding,
    UnconnectedPeopleSearch,
)
from evorove_lead.sqlalchemy_warehouse import warehouse_from_env
from evorove_lead.warehouse import (
    AnalysisWarehouse,
    BriefRecord,
    CandidateRecord,
    HypothesisRecord,
    RejectedTraceRecord,
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
        hypothesis_search: HypothesisPeopleSearch | None = None,
        geo_radius: GeoRadius | None = None,
    ) -> None:
        self._presence = presence
        self._people_search = people_search or UnconnectedPeopleSearch()
        self._policy = policy or LeadGenerationPolicy()
        self._lead_touch_sink = lead_touch_sink if lead_touch_sink is not None else sink_from_env()
        self._warehouse = warehouse if warehouse is not None else warehouse_from_env()
        # `hypothesis_search` is phase 2's real connector: a per-hypothesis
        # `HypothesisPeopleSearch`, not the offer-wide `PeopleSearch` bridge
        # below. When set, it replaces the bridge path entirely.
        self._hypothesis_search = hypothesis_search
        # Explicit, not hardcoded: the contract's default is the client's own
        # city/service area, not the whole US market. Deriving that from the
        # brief automatically is not built yet -- callers pass it in; an
        # unset radius means "whole US market", not a silent narrowing.
        self._geo_radius = geo_radius or GeoRadius()

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

        if self._hypothesis_search is not None:
            return self._generate_via_hypotheses(seed, offer)

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
            handoff = Cycle1Handoff.from_candidate(
                candidate, hit.channel, person_id=person_id, hypothesis_id=hypothesis_id
            )
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

        Only the old offer-wide bridge path (`self._people_search`) uses
        this. The real per-hypothesis connector (`self._hypothesis_search`)
        uses `_generate_via_hypotheses` instead, which persists a real
        `Hypothesis` per audience/trigger rather than one "unstructured"
        stand-in. Kept for backward compatibility with the bridge path
        (phase 0) until every caller has a real connector to hand it.
        """

        if not seed.business_id:
            return ""
        now = datetime.now(timezone.utc)
        brief_id = new_id("brief")
        self._warehouse.save_brief(_brief_record(brief_id, seed.business_id, offer, now))
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

    def _generate_via_hypotheses(
        self, seed: BusinessSeed, offer: OfferUnderstanding
    ) -> GenerationResult:
        """Phase 2's real path: hypothesis -> query -> trace -> re-analysis -> Cold.

        `candidate.py`'s own selection logic (`_accept_hit`) is unchanged --
        this only supplies it real hits instead of a JSONL stub, per the
        contract's "прогнать существующую логику... без изменения самой
        логики отбора."
        """

        if not self._hypothesis_search.connected:
            return GenerationResult(
                status=GenerationStatus.SEARCH_UNCONNECTED,
                offer=offer,
                candidates=(),
                handoffs=(),
                rejected=(),
            )

        business_id = seed.business_id
        now = datetime.now(timezone.utc)
        brief_id = new_id("brief") if business_id else ""
        if business_id:
            self._warehouse.save_brief(_brief_record(brief_id, business_id, offer, now))

        accepted: list[Candidate] = []
        handoffs: list[Cycle1Handoff] = []
        rejected: list[RejectedHit] = []
        # Different hypotheses can turn up the same public post -- one
        # person addressed once, not once per hypothesis that noticed them
        # (contract: "его ещё нет на доске... с тем же контактом").
        seen_identities: set[str] = set()
        for hypothesis in build_hypotheses(offer, self._geo_radius):
            hypothesis_id = new_id("hypothesis") if business_id else ""
            findings = tuple(self._hypothesis_search.find(hypothesis))
            verified = verify_hypothesis(
                hypothesis, _StaticProbe(tuple(f.hit for f in findings if f.hit is not None))
            )
            if business_id:
                self._warehouse.save_hypothesis(
                    _hypothesis_record(hypothesis_id, business_id, brief_id, verified, now)
                )

            for finding in findings:
                trace_id = new_id("trace") if hypothesis_id else ""
                if hypothesis_id:
                    self._warehouse.save_trace(
                        _finding_to_trace_record(trace_id, business_id, hypothesis_id, finding)
                    )
                if finding.hit is None:
                    if hypothesis_id:
                        self._warehouse.save_rejected_trace(
                            _finding_to_rejected_trace_record(trace_id, business_id, finding, now)
                        )
                    continue
                hit = finding.hit
                try:
                    candidate = _accept_hit(hit, offer)
                except CandidateRejected as exc:
                    rejected.append(RejectedHit(identity=hit.identity, why=str(exc)))
                    if hypothesis_id:
                        self._warehouse.save_candidate(
                            _rejected_hit_to_candidate_record(
                                business_id, hypothesis_id, hit, str(exc)
                            )
                        )
                    continue
                identity_key = candidate.identity.strip().casefold()
                if identity_key in seen_identities:
                    if hypothesis_id:
                        self._warehouse.save_candidate(
                            _rejected_hit_to_candidate_record(
                                business_id,
                                hypothesis_id,
                                hit,
                                "duplicate contact already accepted this run",
                            )
                        )
                    continue
                seen_identities.add(identity_key)
                accepted.append(candidate)
                name, phone, email = split_identity(candidate.identity)
                person_id = (
                    stable_person_id(business_id, phone=phone, email=email, identity=candidate.identity)
                    if business_id
                    else ""
                )
                handoff = Cycle1Handoff.from_candidate(
                    candidate, hit.channel, person_id=person_id, hypothesis_id=hypothesis_id
                )
                handoffs.append(handoff)
                if hypothesis_id:
                    self._warehouse.save_candidate(
                        _candidate_to_record(
                            business_id, hypothesis_id, candidate, email=email, phone=phone
                        )
                    )
                if business_id and (phone or email):
                    self._lead_touch_sink.publish(
                        business_id, assembled_touch(business_id, candidate, handoff)
                    )

        status = GenerationStatus.PEOPLE_FOUND if accepted else GenerationStatus.NO_FIT
        return GenerationResult(
            status=status,
            offer=offer,
            candidates=tuple(accepted),
            handoffs=tuple(handoffs),
            rejected=tuple(rejected),
        )


class _StaticProbe:
    """Adapts already-fetched hits into a `HypothesisProbe`.

    The real connector's `find()` result doubles as its own verification
    signal here -- no separate cheap-probe round trip for this first
    connector; a future one can call `verify_hypothesis` before spending
    the full query budget instead.
    """

    def __init__(self, hits: tuple[PeopleHit, ...]) -> None:
        self._hits = hits

    def probe(self, hypothesis: Hypothesis) -> tuple[PeopleHit, ...]:
        del hypothesis
        return self._hits


def _brief_record(brief_id: str, business_id: str, offer: OfferUnderstanding, now: datetime) -> BriefRecord:
    return BriefRecord(
        id=brief_id,
        business_id=business_id,
        what_we_sell=tuple(
            {"text": claim.text, "source_name": claim.source_name} for claim in offer.what_we_sell
        ),
        who_may_fit=tuple(
            {"text": claim.text, "source_name": claim.source_name} for claim in offer.who_may_fit
        ),
        commercial_claims=tuple(
            {"kind": claim.kind, "text": claim.text, "source_name": claim.source_name}
            for claim in offer.commercial_claims
        ),
        must_not_promise=offer.must_not_promise,
        created_at=now,
    )


def _hypothesis_record(
    hypothesis_id: str, business_id: str, brief_id: str, hypothesis: Hypothesis, now: datetime
) -> HypothesisRecord:
    return HypothesisRecord(
        id=hypothesis_id,
        business_id=business_id,
        brief_id=brief_id,
        audience_segment=hypothesis.audience_segment,
        channel=hypothesis.channel,
        query_template=hypothesis.query_template,
        intent_trigger=hypothesis.intent_trigger.kind,
        status=hypothesis.status,
        fit_score=hypothesis.fit_score,
        evidence_score=hypothesis.evidence_score,
        reach_estimate=hypothesis.reach_estimate,
        created_at=now,
    )


def _finding_to_trace_record(
    trace_id: str, business_id: str, hypothesis_id: str, finding: TraceFinding
) -> TraceRecord:
    return TraceRecord(
        id=trace_id,
        business_id=business_id,
        hypothesis_id=hypothesis_id,
        url=finding.url,
        fetched_at=datetime.now(timezone.utc),
        raw_text=finding.raw_text,
        query_used=finding.query_used,
        source_channel=finding.source_channel,
        language=finding.language,
        geo_hint=finding.geo_hint,
    )


def _finding_to_rejected_trace_record(
    trace_id: str, business_id: str, finding: TraceFinding, now: datetime
) -> RejectedTraceRecord:
    return RejectedTraceRecord(
        id=new_id("rejected_trace"),
        business_id=business_id,
        trace_id=trace_id,
        reason=finding.reject_reason,
        rejected_at=now,
    )


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
