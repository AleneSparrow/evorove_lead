"""Turn a grounded offer into testable hypotheses about where to look.

`OfferUnderstanding` is a static portrait: service, audience, market, what
not to promise. A hypothesis is a specific (audience, channel, query) bet,
tagged with *why* a person answering that query would belong here -- their
demographic fit, or a public signal that they need this right now.

This module does not search, does not score people, and does not touch the
warehouse. `verify_hypothesis` runs a cheap probe through a caller-supplied
port and marks empty or garbage hypotheses dead; it never invents a person.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, Sequence

from evorove_lead.candidate import CandidateRejected, accept_candidate
from evorove_lead.offer import OfferUnderstanding
from evorove_lead.search import PeopleHit

INTENT_TRIGGER_KINDS = (
    "public_ask",
    "competitor_complaint",
    "need_statement",
    "demographic_fit",
)


@dataclass(frozen=True)
class IntentTrigger:
    """Why a person surfaced by this hypothesis might belong here.

    `demographic_fit` is a profile guess: they look like the named audience.
    The other three are signals that someone has an open need right now.
    """

    kind: str
    description: str

    def __post_init__(self) -> None:
        if self.kind not in INTENT_TRIGGER_KINDS:
            raise ValueError(f"unknown intent trigger kind {self.kind!r}")
        if not (self.description or "").strip():
            raise ValueError("intent trigger description is required")


@dataclass(frozen=True)
class GeoRadius:
    """Where to look. Market is always the US; locality narrows it.

    Phase 2 decides the default (the client's own city/service area, not
    the whole US market). This module only carries the value through into
    the query template -- it does not pick one.
    """

    country: str = "US"
    locality: str = ""


@dataclass(frozen=True)
class Hypothesis:
    """A testable bet: this audience, on this channel, with this query.

    `status`/`fit_score`/`evidence_score`/`reach_estimate` start unverified
    (`build_hypotheses`) and are filled in by measurement (`verify_hypothesis`),
    never by a guessed weight.
    """

    audience_segment: str
    channel: str
    query_template: str
    intent_trigger: IntentTrigger
    geo_radius: GeoRadius
    status: str = "live"  # "live" | "dead" | "paused"
    fit_score: float = 0.0
    evidence_score: float = 0.0
    reach_estimate: int = 0


class HypothesisProbe(Protocol):
    """A cheap, per-hypothesis check -- not the full `PeopleSearch` parse.

    Phase 2 supplies a real implementation (1-2 trial queries on the
    hypothesis's channel). This is deliberately a separate, narrower port:
    `PeopleSearch.find` takes a whole offer, not one hypothesis's query.
    """

    def probe(self, hypothesis: Hypothesis) -> Sequence[PeopleHit]:
        """Return a few raw hits for this hypothesis's query. Empty is allowed."""


def _geo_suffix(geo_radius: GeoRadius) -> str:
    return f" near {geo_radius.locality}" if geo_radius.locality else ""


def build_hypotheses(
    offer: OfferUnderstanding, geo_radius: GeoRadius
) -> tuple[Hypothesis, ...]:
    """Derive testable hypotheses from a grounded offer.

    At least one hypothesis per named audience (demographic fit, as
    before), plus two built around a person's own public signal of need
    for the primary service. Every hypothesis is traceable to a claim
    that was already grounded in the owner's own materials -- nothing
    here invents an audience or a service the offer didn't already state.
    """

    if not offer.what_we_sell:
        raise ValueError("offer has no service to build a hypothesis around")
    if not offer.who_may_fit:
        raise ValueError("offer has no audience to build a hypothesis around")

    service = offer.what_we_sell[0].text
    geo_suffix = _geo_suffix(geo_radius)

    hypotheses: list[Hypothesis] = []
    for claim in offer.who_may_fit:
        hypotheses.append(
            Hypothesis(
                audience_segment=claim.text,
                channel="web_search",
                query_template=f"{service} for {claim.text}{geo_suffix}".strip(),
                intent_trigger=IntentTrigger(
                    kind="demographic_fit",
                    description=f"Named in the brief as who this may fit: {claim.text}",
                ),
                geo_radius=geo_radius,
            )
        )

    primary_audience = offer.who_may_fit[0].text
    trigger_hypotheses = (
        (
            "public_ask",
            f'"looking for {service}"{geo_suffix}'.strip(),
            "Publicly asks for this service right now",
        ),
        (
            "need_statement",
            f'"need {service}"{geo_suffix}'.strip(),
            "Publicly describes needing this service",
        ),
    )
    for kind, query_template, description in trigger_hypotheses:
        hypotheses.append(
            Hypothesis(
                audience_segment=primary_audience,
                channel="web_search",
                query_template=query_template,
                intent_trigger=IntentTrigger(kind=kind, description=description),
                geo_radius=geo_radius,
            )
        )

    return tuple(hypotheses)


def verify_hypothesis(hypothesis: Hypothesis, probe: HypothesisProbe) -> Hypothesis:
    """Run a cheap probe and mark the hypothesis dead if it finds nothing real.

    A hit "counts" only if it clears the same floor as a candidate: someone
    to address, a reason, and a source for that reason (`candidate.py`'s
    invariant, applied before any offer-fit check -- this is the lightweight
    version the contract calls for, not full re-analysis).
    """

    hits = probe.probe(hypothesis)
    valid = 0
    for hit in hits:
        try:
            accept_candidate(
                identity=hit.identity,
                reason=hit.observed_fact,
                reason_source=hit.observed_source,
            )
        except CandidateRejected:
            continue
        valid += 1

    if valid == 0:
        return replace(hypothesis, status="dead", fit_score=0.0, evidence_score=0.0, reach_estimate=0)
    return replace(
        hypothesis,
        status="live",
        fit_score=1.0,
        evidence_score=valid / len(hits),
        reach_estimate=valid,
    )


def prioritize_hypotheses(hypotheses: Sequence[Hypothesis]) -> tuple[Hypothesis, ...]:
    """Rank live hypotheses by measured fit/evidence. No invented weights.

    Dead hypotheses (or ones never verified, still at their build-time
    zero scores) are dropped -- phase 2 only spends budget on what
    `verify_hypothesis` already found something real for.
    """

    live = [h for h in hypotheses if h.status == "live" and h.reach_estimate > 0]
    return tuple(
        sorted(
            live,
            key=lambda h: (h.fit_score, h.evidence_score, h.reach_estimate),
            reverse=True,
        )
    )
