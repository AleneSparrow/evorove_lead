"""Stable ICP identity and public-need query plans; no industry branches."""
from __future__ import annotations
import re
from pydantic import Field
from typing import Literal
from evorove_lead.evidence import ClaimId, StrictModel, digest
from evorove_lead.hypothesis import Hypothesis, IntentTrigger, GeoRadius

class ICPHypothesis(StrictModel):
    segment: str
    buyer_role: str
    potential_problem: str
    solution_mechanism: str
    signals: tuple[str, ...] = Field(min_length=1)
    evidence_requirements: tuple[str, ...] = Field(min_length=1)
    disqualifiers: tuple[str, ...]
    geography: str
    search_strategy: tuple[str, ...] = Field(min_length=1)
    attractiveness_assumptions: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)
    supporting_claims: tuple[ClaimId, ...] = Field(min_length=1)
    buyer_type: Literal["company", "person"] = "company"
    version: int = Field(default=1, ge=1)

    @property
    def stable_key(self):
        return digest("\n".join((self.segment.casefold(), self.buyer_role.casefold(), self.potential_problem.casefold(), self.geography.casefold(), self.buyer_type)))

class HypothesisSet(StrictModel):
    hypotheses: tuple[ICPHypothesis, ...] = Field(min_length=1, max_length=4)

class ICPHypothesisEngine:
    def __init__(self, reasoner):
        self.reasoner = reasoner

    def generate(self, dna, *, market=None):
        result = self.reasoner.ask("Name buyers who would say this problem in public. supporting_claims are supplied DNA or market claim ids. Market conclusions guide buyer hypotheses, never establish a particular prospect's need. Use concise search-ready noun phrases for segment, potential_problem and signals; search_strategy should contain short natural web queries with source operators where useful. Derive signals from this business and market, not a fixed looking-for template. Do not write survey questions.", HypothesisSet, {"dna": dna.model_dump(mode="json"), "market": market.model_dump(mode="json") if market is not None else None})
        claims = {c.id for c in dna.evidence.claims}
        if market is not None:
            market.validate_grounding(dna.business_id, market.evidence.sources)
            claims.update(c.id for c in market.evidence.claims)
        if any(not set(h.supporting_claims) <= claims for h in result.hypotheses):
            raise ValueError("hypothesis references invented evidence")
        # Confidence is a ranking hint, never purchase probability or gate input.
        return tuple(sorted(result.hypotheses, key=lambda h: (-len(set(h.supporting_claims)), -h.confidence, h.stable_key)))

_SURVEY = re.compile(r"\b(what|why|how|which|who|percentage|percent|roi)\b", re.I)


def is_buyer_query(query: str) -> bool:
    """A search for someone who already stated a need. Not a market question."""
    text = " ".join((query or "").split())
    if len(text) < 8 or len(text) > 200 or "?" in text:
        return False
    if "linkedin.com" in text.casefold():
        return False
    return _SURVEY.search(text) is None


def _clip(value: str) -> str:
    return " ".join(value.split()).split(".")[0].strip()[:80]


# Remove explanatory scaffolding, not business-specific vocabulary. No industry map.
_QUERY_NOISE = set("a an the in on at for of to and or that who which with are is be may can would should say says publicly public owner founder statement statements including about search forums vendor claimed market statistics apply much too".split())


def _query_terms(value: str, limit: int) -> str:
    first_sentence = re.split(r"[.!?](?:\s|$)", value, maxsplit=1)[0]
    tokens = re.findall(r"site:[A-Za-z0-9./_-]+|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", first_sentence)
    kept = []
    for token in tokens:
        if token.casefold() not in _QUERY_NOISE and token.casefold() not in {t.casefold() for t in kept}:
            kept.append(token)
    return " ".join(kept[:limit])


def buyer_query_set(problem: str, segment: str, place: str, *, signals=(), strategies=()) -> tuple[str, ...]:
    segment = _query_terms(segment, 4)
    problem = _query_terms(problem, 5)
    place = " ".join((place or "United States").split())
    if not problem or not segment:
        return ()
    # Each query keeps audience + geography; signals and sources come from ICP.
    candidates = [f"{segment} {problem}"]
    candidates.extend(f"{segment} {_query_terms(signal, 5)}" for signal in signals)
    candidates.extend(f"{_query_terms(strategy, 7)} {segment}" for strategy in strategies)
    kept = []
    for query in candidates:
        query = f"{query[:max(0, 119 - len(place))].rstrip()} {place}"
        if is_buyer_query(query) and query not in kept:
            kept.append(query)
    return tuple(kept[:4])


def buyer_queries_for(hypothesis: ICPHypothesis) -> tuple[str, ...]:
    return buyer_query_set(hypothesis.potential_problem, hypothesis.segment, hypothesis.geography,
                           signals=hypothesis.signals, strategies=hypothesis.search_strategy)


def buyer_queries_from_dna(dna) -> tuple[str, ...]:
    claims = {c.id: c.text for c in dna.evidence.claims}
    def texts(field):
        return [_clip(claims[ref]) for ref in getattr(dna, field) if claims.get(ref, "").strip()]
    problems = texts("problems") or texts("services")
    audiences = texts("customer_categories")
    if not problems or not audiences:
        return ()
    place = dna.service_localities[0] if dna.service_localities else "United States"
    return buyer_query_set(problems[0], audiences[0], place)


class QueryPlan(StrictModel):
    queries: tuple[str, ...] = Field(min_length=1, max_length=3)
    rationale: str

class SearchPlanner:
    def __init__(self, reasoner):
        self.reasoner = reasoner

    def plan(self, hypothesis: ICPHypothesis) -> QueryPlan:
        return self.reasoner.ask("Design targeted search queries combining geography and a buying signal or evidence requirement; do not return generic industry queries.", QueryPlan, {"hypothesis": hypothesis.model_dump(mode="json")})

    @staticmethod
    def adapt(hypothesis: ICPHypothesis, query: str) -> Hypothesis:
        return Hypothesis(audience_segment=hypothesis.segment, channel="web_search", query_template=query, intent_trigger=IntentTrigger(kind="business_listing" if hypothesis.buyer_type == "company" else "need_statement", description=hypothesis.solution_mechanism), geo_radius=GeoRadius(locality=hypothesis.geography))
