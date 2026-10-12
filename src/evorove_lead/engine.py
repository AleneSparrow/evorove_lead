"""Lead generation engine: see the business, understand the offer, keep people with a reason.

Does not message the person, book a slot, or run a sales conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence

from evorove_lead.business import BusinessSeed
from evorove_lead.candidate import Candidate, CandidateRejected, accept_candidate
from evorove_lead.cost_control import BudgetExhausted, RunLedger, client_budget_blocked
from evorove_lead.qualification import confirm_inferred, qualify
from evorove_lead.ranking import rank_score
from evorove_lead.selection import (
    ICP_MATCH,
    INFERRED_OPPORTUNITY,
    INSUFFICIENT_EVIDENCE,
    KEYWORD_ONLY,
    SOURCE_KIND_BUSINESS,
    Selection,
    SelectionProfile,
    select,
)
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.geo import infer_geo_radius
from evorove_lead.hypothesis import GeoRadius, Hypothesis, build_hypotheses, verify_hypothesis
from evorove_lead.marketing_analysis import (
    MarketingAnalysis,
    MarketingAnalysisRejected,
    MarketingAnalyzer,
    analysis_as_dict,
    hypotheses_from_analysis,
)
from evorove_lead.materials import DepositedMaterial
from evorove_lead.offer import OfferRejected, OfferUnderstanding
from evorove_lead.offer_reader import read_offer
from evorove_lead.pattern_library import PatternLibrary
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
from evorove_lead.sqlalchemy_pattern_library import pattern_library_from_env
from evorove_lead.sqlalchemy_warehouse import warehouse_from_env
from evorove_lead.warehouse import (
    AnalysisWarehouse,
    BriefRecord,
    CandidateRecord,
    HypothesisRecord,
    RejectedTraceRecord,
    SpendRecord,
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
    cost: dict | None = None
    stopped_reason: str = ""

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
        pattern_library: PatternLibrary | None = None,
        infer_geo_radius_from_brief: bool = False,
        marketing_analyzer: MarketingAnalyzer | None = None,
        run_budget: RunLedger | None = None,
        classifier=None,
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
        # An explicit `geo_radius` always wins. Otherwise, opt into
        # `geo.infer_geo_radius` reading the client's own city/zone out of
        # their materials (`infer_geo_radius_from_brief=True`) -- off by
        # default, because a text heuristic can misfire and silently
        # narrowing production search scope is worse than the honest,
        # wide default. Neither set: whole US market, not a guess.
        self._explicit_geo_radius = geo_radius
        self._infer_geo_radius_from_brief = infer_geo_radius_from_brief
        # Phase 4: system-wide, non-tenant. Only ever consulted when
        # `seed.business_archetype` is set -- the engine never invents one.
        self._pattern_library = pattern_library if pattern_library is not None else pattern_library_from_env()
        # None keeps the literal "serves X" audience path. A set analyzer
        # replaces those search audiences with grounded inferences.
        self._marketing_analyzer = marketing_analyzer
        self._budget = run_budget
        # Optional cheap confirmer for inferred rows. None keeps selection deterministic.
        self._classifier = classifier
        self._stopped = ""
        self._accounted = SpendRecord(business_id="")

    def generate(
        self, seed: BusinessSeed, *, extra_hypotheses: Sequence[Hypothesis] = ()
    ) -> GenerationResult:
        # `extra_hypotheses` is track 4's hook: a market-signal run appends
        # one ad-hoc, already-built Hypothesis here instead of re-deriving
        # the whole offer/analysis-driven set for a single news event. Empty
        # by default -- every existing caller is unaffected.
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

        analysis: MarketingAnalysis | None = None
        offer = None
        try:
            if self._marketing_analyzer is not None:
                offer = read_offer(materials, require_audience=False)
                analysis = self._marketing_analyzer.analyze(materials)
            else:
                offer = read_offer(materials)
        except (OfferRejected, MarketingAnalysisRejected):
            return self._finish(
                GenerationResult(
                    status=GenerationStatus.OFFER_INCOMPLETE,
                    offer=None,
                    candidates=(),
                    handoffs=(),
                    rejected=(),
                ),
                processed=0,
                qualified=0,
                business_id=seed.business_id,
            )
        except BudgetExhausted:
            self._stopped = self._budget.stopped_reason if self._budget else "budget_exhausted"
            return self._finish(
                GenerationResult(
                    status=GenerationStatus.NO_FIT,
                    offer=offer,
                    candidates=(),
                    handoffs=(),
                    rejected=(),
                ),
                processed=0,
                qualified=0,
                business_id=seed.business_id,
            )

        if analysis is not None:
            if self._hypothesis_search is None:
                return GenerationResult(
                    status=GenerationStatus.SEARCH_UNCONNECTED,
                    offer=offer,
                    candidates=(),
                    handoffs=(),
                    rejected=(),
                )
            return self._generate_via_hypotheses(
                seed, offer, materials, analysis, extra_hypotheses=extra_hypotheses
            )

        if self._hypothesis_search is not None:
            return self._generate_via_hypotheses(
                seed, offer, materials, extra_hypotheses=extra_hypotheses
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

        blocked = self._finish_if_blocked(seed.business_id, offer)
        if blocked is not None:
            return blocked
        try:
            self._charge_search()
        except BudgetExhausted:
            return self._stopped_result(offer, seed.business_id)

        hypothesis_id = self._record_brief_and_hypothesis(seed, offer)
        profile = SelectionProfile.from_offer(offer, materials)
        seen_identities = self._already_handed_over(seed.business_id)

        ranked: list[tuple[float, Candidate, Cycle1Handoff]] = []
        rejected: list[RejectedHit] = []
        for hit in self._people_search.find(offer):
            if hypothesis_id:
                self._warehouse.save_trace(_hit_to_trace_record(seed.business_id, hypothesis_id, hit))
            candidate, selection = _judge_hit(hit, profile, self._classifier)
            if candidate is None:
                rejected.append(RejectedHit(identity=hit.identity, why=selection.why))
                if hypothesis_id:
                    self._warehouse.save_candidate(
                        _rejected_hit_to_candidate_record(
                            seed.business_id, hypothesis_id, hit, selection.why, selection.evidence_class
                        )
                    )
                continue
            if not _remember(candidate, seen_identities):
                self._reject_duplicate(seed.business_id, hypothesis_id, hit, rejected)
                continue
            if seed.business_id and not self._warehouse.claim_cold_identity(
                seed.business_id, candidate.identity
            ):
                self._reject_duplicate(seed.business_id, hypothesis_id, hit, rejected)
                continue
            handoff = self._keep(
                seed.business_id, hypothesis_id, candidate, hit, selection, ranked, segment=None
            )
            del handoff

        accepted, handoffs = _ordered(ranked)
        status = GenerationStatus.PEOPLE_FOUND if accepted else GenerationStatus.NO_FIT
        return self._finish(
            GenerationResult(
                status=status,
                offer=offer,
                candidates=accepted,
                handoffs=handoffs,
                rejected=tuple(rejected),
            ),
            processed=len(accepted) + len(rejected),
            qualified=len(accepted),
            business_id=seed.business_id,
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
        self._warehouse.save_brief(
            _brief_record(brief_id, seed.business_id, offer, now, seed.business_archetype)
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

    def _already_handed_over(self, business_id: str) -> set[str]:
        if not business_id:
            return set()
        return {identity.strip().casefold() for identity in self._warehouse.accepted_identities(business_id)}

    def _resolve_geo_radius(self, materials: tuple[DepositedMaterial, ...]) -> GeoRadius:
        if self._explicit_geo_radius is not None:
            return self._explicit_geo_radius
        if self._infer_geo_radius_from_brief:
            return infer_geo_radius(materials)
        return GeoRadius()

    def _generate_via_hypotheses(
        self,
        seed: BusinessSeed,
        offer: OfferUnderstanding,
        materials: tuple[DepositedMaterial, ...],
        analysis: MarketingAnalysis | None = None,
        *,
        extra_hypotheses: Sequence[Hypothesis] = (),
    ) -> GenerationResult:
        """Phase 2's real path: hypothesis -> query -> trace -> re-analysis -> Cold.

        Selection still decides Cold. This path only supplies real hits,
        skips a pattern the warehouse has already paused, and stops when
        the run budget is spent.
        """

        if not self._hypothesis_search.connected:
            return GenerationResult(
                status=GenerationStatus.SEARCH_UNCONNECTED,
                offer=offer,
                candidates=(),
                handoffs=(),
                rejected=(),
            )
        blocked = self._finish_if_blocked(seed.business_id, offer)
        if blocked is not None:
            return blocked

        business_id = seed.business_id
        now = datetime.now(timezone.utc)
        brief_id = new_id("brief") if business_id else ""
        if business_id:
            self._warehouse.save_brief(
                _brief_record(
                    brief_id,
                    business_id,
                    offer,
                    now,
                    seed.business_archetype,
                    analysis_as_dict(analysis) if analysis is not None else None,
                )
            )

        ranked: list[tuple[float, Candidate, Cycle1Handoff]] = []
        rejected: list[RejectedHit] = []
        # Different hypotheses can turn up the same public post -- one
        # person addressed once, not once per hypothesis that noticed them
        # (contract: "его ещё нет на доске... с тем же контактом"), and
        # nobody already handed to the board by an earlier run (step 19).
        seen_identities = self._already_handed_over(business_id)
        geo_radius = self._resolve_geo_radius(materials)
        if analysis is not None:
            profile = SelectionProfile.from_audience_texts(
                offer, [segment.label for segment in analysis.segments], materials
            )
            hypotheses = hypotheses_from_analysis(
                offer,
                analysis,
                geo_radius,
                pattern_library=self._pattern_library,
                business_archetype=seed.business_archetype,
            )
        else:
            profile = SelectionProfile.from_offer(offer, materials)
            hypotheses = build_hypotheses(
                offer,
                geo_radius,
                pattern_library=self._pattern_library,
                business_archetype=seed.business_archetype,
            )
        hypotheses = (*hypotheses, *extra_hypotheses)
        for hypothesis in hypotheses:
            if self._stopped:
                break
            if _pattern_paused(self._warehouse, business_id, hypothesis):
                continue
            try:
                self._charge_search()
            except BudgetExhausted:
                self._stopped = self._budget.stopped_reason if self._budget else "budget_exhausted"
                break
            hypothesis_id = new_id("hypothesis") if business_id else ""
            try:
                findings = tuple(self._hypothesis_search.find(hypothesis))
            except BudgetExhausted:
                self._stopped = self._budget.stopped_reason if self._budget else "budget_exhausted"
                break
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
                candidate, selection = _judge_hit(hit, profile, self._classifier, strict=True)
                if candidate is None:
                    rejected.append(RejectedHit(identity=hit.identity, why=selection.why))
                    if hypothesis_id:
                        self._warehouse.save_candidate(
                            _rejected_hit_to_candidate_record(
                                business_id, hypothesis_id, hit, selection.why, selection.evidence_class
                            )
                        )
                    continue
                if not _remember(candidate, seen_identities):
                    self._reject_duplicate(business_id, hypothesis_id, hit, rejected)
                    continue
                if business_id and not self._warehouse.claim_cold_identity(business_id, candidate.identity):
                    self._reject_duplicate(business_id, hypothesis_id, hit, rejected)
                    continue
                self._keep(
                    business_id,
                    hypothesis_id,
                    candidate,
                    hit,
                    selection,
                    ranked,
                    segment=hypothesis.audience_segment,
                )

        accepted, handoffs = _ordered(ranked)
        status = GenerationStatus.PEOPLE_FOUND if accepted else GenerationStatus.NO_FIT
        return self._finish(
            GenerationResult(
                status=status,
                offer=offer,
                candidates=accepted,
                handoffs=handoffs,
                rejected=tuple(rejected),
            ),
            processed=len(accepted) + len(rejected),
            qualified=len(accepted),
            business_id=business_id,
        )

    def _client_block(self, business_id: str) -> str:
        if not business_id:
            return ""
        spent = self._warehouse.spend_for(business_id)
        return client_budget_blocked(spent.llm_calls, spent.search_requests)

    def _finish_if_blocked(self, business_id: str, offer: OfferUnderstanding) -> GenerationResult | None:
        reason = self._client_block(business_id)
        if not reason:
            return None
        self._stopped = reason
        return self._finish(
            GenerationResult(
                status=GenerationStatus.NO_FIT,
                offer=offer,
                candidates=(),
                handoffs=(),
                rejected=(),
            ),
            processed=0,
            qualified=0,
            business_id=business_id,
        )

    def _charge_search(self) -> None:
        if self._budget is not None:
            self._budget.charge_search("discovery")

    def _stopped_result(self, offer: OfferUnderstanding | None, business_id: str) -> GenerationResult:
        self._stopped = self._budget.stopped_reason if self._budget else "budget_exhausted"
        return self._finish(
            GenerationResult(
                status=GenerationStatus.NO_FIT,
                offer=offer,
                candidates=(),
                handoffs=(),
                rejected=(),
            ),
            processed=0,
            qualified=0,
            business_id=business_id,
        )

    def _reject_duplicate(
        self, business_id: str, hypothesis_id: str, hit: PeopleHit, rejected: list[RejectedHit]
    ) -> None:
        why = "duplicate contact already handed to the board"
        rejected.append(RejectedHit(identity=hit.identity, why=why))
        if hypothesis_id:
            self._warehouse.save_candidate(
                _rejected_hit_to_candidate_record(business_id, hypothesis_id, hit, why, "")
            )

    def _keep(
        self,
        business_id: str,
        hypothesis_id: str,
        candidate: Candidate,
        hit: PeopleHit,
        selection: Selection,
        ranked: list[tuple[float, Candidate, Cycle1Handoff]],
        *,
        segment: str | None,
    ) -> None:
        name, phone, email = split_identity(candidate.identity)
        person_id = (
            stable_person_id(business_id, phone=phone, email=email, identity=candidate.identity)
            if business_id
            else ""
        )
        handoff = Cycle1Handoff.from_candidate(
            candidate, hit.channel, person_id=person_id, hypothesis_id=hypothesis_id
        )
        if hypothesis_id:
            self._warehouse.save_candidate(
                _candidate_to_record(
                    business_id,
                    hypothesis_id,
                    candidate,
                    email=email,
                    phone=phone,
                    selection=selection,
                )
            )
        if business_id and (phone or email):
            self._lead_touch_sink.publish(
                business_id,
                assembled_touch(business_id, candidate, handoff, segment=segment),
            )
        ranked.append(
            (
                rank_score(
                    evidence_class=selection.evidence_class,
                    fit=selection.fit,
                    evidence=selection.evidence,
                ),
                candidate,
                handoff,
            )
        )

    def _finish(
        self,
        result: GenerationResult,
        *,
        processed: int,
        qualified: int,
        business_id: str,
    ) -> GenerationResult:
        report = None
        if self._budget is not None:
            report = self._budget.report(processed=processed, qualified=qualified)
            if business_id:
                delta = SpendRecord(
                    business_id=business_id,
                    llm_calls=self._budget.llm_calls - self._accounted.llm_calls,
                    search_requests=self._budget.search_requests - self._accounted.search_requests,
                    input_tokens=self._budget.input_tokens - self._accounted.input_tokens,
                    output_tokens=self._budget.output_tokens - self._accounted.output_tokens,
                    unknown_price=self._budget.unknown_price,
                )
                self._accounted = SpendRecord(
                    business_id=business_id,
                    llm_calls=self._budget.llm_calls,
                    search_requests=self._budget.search_requests,
                    input_tokens=self._budget.input_tokens,
                    output_tokens=self._budget.output_tokens,
                    unknown_price=self._budget.unknown_price,
                )
                if delta.llm_calls or delta.search_requests or delta.input_tokens or delta.output_tokens:
                    self._warehouse.add_spend(delta)
        return replace(result, cost=report, stopped_reason=self._stopped)


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


def _brief_record(
    brief_id: str,
    business_id: str,
    offer: OfferUnderstanding,
    now: datetime,
    business_archetype: str = "",
    marketing_analysis: dict | None = None,
) -> BriefRecord:
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
        business_archetype=business_archetype,
        marketing_analysis=marketing_analysis,
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
        audience_source=hypothesis.audience_source,
        evidence_quote=hypothesis.evidence_quote,
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


def _judge_hit(
    hit: PeopleHit, profile: SelectionProfile, classifier, *, strict: bool = False
) -> tuple[Candidate | None, Selection]:
    """Candidate floor, then fit / evidence / addressable.

    `strict` is the live hypothesis path: a person matched only by a keyword
    or an audience label is not purchase intent and does not reach Cold.
    The older bridge path leaves that gate off so owner-deposited facts that
    the existing tests lock in still pass.
    """

    try:
        candidate = accept_candidate(
            identity=hit.identity, reason=hit.observed_fact, reason_source=hit.observed_source
        )
    except CandidateRejected as exc:
        return None, Selection(0.0, 0.0, False, False, str(exc), INSUFFICIENT_EVIDENCE)
    _, phone, email = split_identity(candidate.identity)
    selection = select(
        candidate.reason, profile, addressable=bool(phone or email), source_kind=hit.source_kind
    )
    if classifier is not None and selection.evidence_class == INFERRED_OPPORTUNITY:
        confirmed = confirm_inferred(
            candidate.reason,
            qualify(
                candidate.reason,
                profile,
                addressable=bool(phone or email),
                source_kind=hit.source_kind,
                source_url=hit.observed_source,
            ),
            classifier,
        )
        selection = Selection(
            confirmed.fit,
            confirmed.evidence,
            bool(phone or email),
            confirmed.accepted,
            confirmed.why,
            confirmed.evidence_class,
        )
    if (
        strict
        and hit.source_kind != SOURCE_KIND_BUSINESS
        and selection.evidence_class in (ICP_MATCH, INSUFFICIENT_EVIDENCE)
    ):
        selection = Selection(
            selection.fit,
            selection.evidence,
            selection.addressable,
            False,
            KEYWORD_ONLY,
            selection.evidence_class,
        )
    if not selection.accepted:
        return None, selection
    return candidate, selection


def _remember(candidate: Candidate, seen: set[str]) -> bool:
    key = candidate.identity.strip().casefold()
    if key in seen:
        return False
    seen.add(key)
    return True


def _ordered(
    ranked: list[tuple[float, Candidate, Cycle1Handoff]],
) -> tuple[tuple[Candidate, ...], tuple[Cycle1Handoff, ...]]:
    ranked.sort(key=lambda item: item[0], reverse=True)
    return tuple(item[1] for item in ranked), tuple(item[2] for item in ranked)


def _pattern_paused(warehouse: AnalysisWarehouse, business_id: str, hypothesis: Hypothesis) -> bool:
    """Skip a query the last stored row for this exact template already paused or zeroed."""

    if not business_id:
        return False
    matches = [
        row
        for row in warehouse.list_hypotheses(business_id)
        if row.intent_trigger == hypothesis.intent_trigger.kind
        and row.query_template == hypothesis.query_template
    ]
    if not matches:
        return False
    latest = max(matches, key=lambda row: row.created_at)
    return latest.status == "paused" or latest.query_budget <= 0


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
    selection: Selection | None = None,
) -> CandidateRecord:
    return CandidateRecord(
        id=new_id("candidate"),
        business_id=business_id,
        hypothesis_id=hypothesis_id,
        identity=candidate.identity,
        channel="email" if email else "phone" if phone else "unknown",
        reason=candidate.reason,
        reason_source=candidate.reason_source,
        fit=selection.fit if selection else 1.0,
        evidence=selection.evidence if selection else 1.0,
        addressable=bool(email or phone),
        decision="cold",
        decided_at=datetime.now(timezone.utc),
        email=email or "",
        phone=phone or "",
        evidence_class=selection.evidence_class if selection else "",
    )


def _rejected_hit_to_candidate_record(
    business_id: str, hypothesis_id: str, hit: PeopleHit, why: str, evidence_class: str = ""
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
        evidence_class=evidence_class,
    )
