"""Independent structured assessment/review and a fail-closed deterministic gate."""
from __future__ import annotations
from datetime import datetime, timedelta
from enum import Enum
import re
from pydantic import Field
from typing import Literal
from evorove_lead.evidence import ClaimId, StrictModel, EvidenceBundle, utcnow, company_key
from evorove_lead.crm_touch import EMAIL_RE

class Decision(str, Enum):
    QUALIFIED = "QUALIFIED"
    RESEARCH_MORE = "RESEARCH_MORE"
    REJECTED = "REJECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

class Dimension(StrictModel):
    score: float | None = Field(default=None, ge=0, le=1)
    claims: tuple[ClaimId, ...] = ()
    explanation: str

class QualificationAssessment(StrictModel):
    product_fit: Dimension
    need_evidence: Dimension
    timing: Dimension
    evidence_reliability: Dimension
    contact_relevance: Dimension
    commercial_opportunity: Dimension
    category_claim: ClaimId
    geography_claim: ClaimId
    geography_country: Literal["US", "OTHER", "UNKNOWN"] = "UNKNOWN"
    geography_locality: str = ""
    reason_claims: tuple[ClaimId, ...]
    supported_claims: tuple[ClaimId, ...]
    disqualifying_claims: tuple[ClaimId, ...]
    unknowns: tuple[str, ...]

class AdversarialReview(StrictModel):
    supported_claims: tuple[ClaimId, ...]
    unsupported_claims: tuple[ClaimId, ...]
    critical_contradictions: tuple[ClaimId, ...]
    disqualifying_claims: tuple[ClaimId, ...]
    alternative_explanations: tuple[str, ...]
    contact_appropriate: bool
    category_permitted: bool
    geography_compatible: bool
    need_demonstrated: bool
    additional_queries: tuple[str, ...] = Field(default=(), max_length=2)
    explanation: str

class GateResult(StrictModel):
    decision: Decision
    codes: tuple[str, ...]
    reason: str = ""
    reason_source: str = ""
    policy_version: str = "gate-1"

class LeadQualificationEngine:
    def __init__(self, reasoner):
        self.reasoner = reasoner

    def qualify(self, dna, hypothesis, bundle):
        assessment = self.reasoner.ask("Assess six independent dimensions. Cite claim IDs. Need must be demonstrated for THIS subject. Do not infer revenue or purchasing budget.", QualificationAssessment, {"dna": dna.model_dump(mode="json"), "hypothesis": hypothesis.model_dump(mode="json"), "evidence": bundle.model_dump(mode="json")})
        return assessment

class AdversarialValidation:
    def __init__(self, reasoner):
        self.reasoner = reasoner

    def review(self, dna, hypothesis, bundle, assessment):
        return self.reasoner.ask("Independently challenge qualification. Check each material claim for semantic entailment by its quotes, correct subject and current applicability. Identify alternative explanations, exclusions, unsupported claims and critical contradictions. Missing evidence is not negative evidence. Explicitly validate category, geography, demonstrated need and contact relationship.", AdversarialReview, {"dna": dna.model_dump(mode="json"), "hypothesis": hypothesis.model_dump(mode="json"), "evidence": bundle.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json")})

class QualificationGate:
    def __init__(self, *, max_source_age_days=30, max_signal_age_days=90):
        self.max_source_age = timedelta(days=max_source_age_days)
        self.max_signal_age = timedelta(days=max_signal_age_days)

    def evaluate(self, *, dna, bundle: EvidenceBundle | None, assessment: QualificationAssessment | None,
                 review: AdversarialReview | None, duplicate=False, now=None, research_remaining=False) -> GateResult:
        now = now or utcnow()
        def fail(code, reject=False):
            return GateResult(decision=Decision.REJECTED if reject else Decision.RESEARCH_MORE if research_remaining else Decision.INSUFFICIENT_EVIDENCE, codes=(code,))
        if duplicate:
            return fail("duplicate_contact", True)
        if dna is None or bundle is None or assessment is None or review is None:
            return fail("missing_verified_evidence")
        try:
            dna.validate_grounding()
            bundle = EvidenceBundle.model_validate(bundle.model_dump())
        except ValueError:
            return fail("invalid_evidence")
        if getattr(dna, "service_country", "UNKNOWN") != "US" or assessment.geography_country != "US":
            return fail("country_not_confirmed")
        if bundle.business_id != dna.business_id:
            return fail("tenant_mismatch", True)
        if review.disqualifying_claims or assessment.disqualifying_claims:
            return fail("disqualifying_evidence", True)
        if review.critical_contradictions:
            return fail("critical_contradiction")
        if not all((review.category_permitted, review.geography_compatible, review.contact_appropriate, review.need_demonstrated)):
            return fail("unconfirmed_category_geography_need_or_contact")
        contact = bundle.contact
        if not contact or contact.generic or contact.restricted:
            return fail("contact_missing_generic_or_restricted", bool(contact and contact.restricted))
        identity = contact.identity.strip()
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", identity) and not (identity.startswith("+") and identity[1:].isdigit() and 8 <= len(identity) <= 16):
            return fail("invalid_contact")
        if "@" in identity and identity.split("@")[0].lower() in {"info", "admin", "support", "hello", "contact", "sales", "office", "noreply"}:
            return fail("generic_contact")
        claims = {c.id: c for c in bundle.claims}
        sources = {s.id: s for s in bundle.sources}
        dimensions = (assessment.product_fit, assessment.need_evidence, assessment.timing, assessment.evidence_reliability, assessment.contact_relevance)
        if any(not d.claims for d in dimensions) or not assessment.reason_claims:
            return fail("missing_required_dimension_evidence")
        refs = {assessment.category_claim, assessment.geography_claim, *assessment.reason_claims,
                contact.name_claim, contact.address_claim, contact.relationship_claim,
                *(ref for d in dimensions for ref in d.claims), *assessment.commercial_opportunity.claims}
        if bundle.subject_kind == "company" or contact.role_claim:
            refs.add(contact.role_claim)
        if not refs <= set(claims):
            return fail("unknown_claim_reference")
        if not refs <= set(assessment.supported_claims) & set(review.supported_claims) or refs & set(review.unsupported_claims):
            return fail("unsupported_material_claim")
        geo_claim = claims[assessment.geography_claim]
        geo_quotes = " ".join(l.quote for l in geo_claim.sources if l.relationship == "supports")
        if not re.search(r"\bUS\b|\bUSA\b|United States|U\.S\.", geo_quotes):
            return fail("country_not_grounded")
        permitted_places = getattr(dna, "service_localities", ())
        if permitted_places:
            if assessment.geography_locality.casefold() not in {p.casefold() for p in permitted_places} or assessment.geography_locality.casefold() not in geo_quotes.casefold():
                return fail("locality_not_grounded_or_outside_service_area")
        for ref in refs:
            claim = claims[ref]
            if claim.subject != bundle.subject or claim.assertion_type not in {"OBSERVED_FACT", "BUSINESS_CLAIM"}:
                return fail("unverified_subject_or_inference")
            supports = [link for link in claim.sources if link.relationship == "supports"]
            if not supports:
                return fail("missing_support")
            if any(link.relationship == "contradicts" for link in claim.sources):
                return fail("unresolved_contradiction")
            for link in supports:
                source = sources[link.source_id]
                if not source.public or source.status_code != 200 or source.source_type in {"search_snippet", "owner_material"}:
                    return fail("invalid_source")
                if source.source_type == "first_party" and bundle.subject_kind == "company" and company_key(source.final_url) != bundle.subject:
                    return fail("company_domain_mismatch")
                if source.subject != bundle.subject or company_key(source.final_url) == company_key(dna.site_url):
                    return fail("wrong_or_own_subject")
                if source.fetched_at > now + timedelta(minutes=5) or now - source.fetched_at > self.max_source_age:
                    return fail("stale_source")
                if source.published_at and source.published_at > now:
                    return fail("future_publication_date")
                if source.event_at and (source.event_at > now or now - source.event_at > self.max_signal_age):
                    return fail("outdated_signal")
        # Published contact belongs to the person; mere presence elsewhere on a page is insufficient.
        bindings = [(contact.name_claim, contact.person_name), (contact.address_claim, identity)]
        if bundle.subject_kind == "company":
            bindings.append((contact.role_claim, contact.role))
        for ref, literal in bindings:
            if not literal or not any(literal.casefold() in link.quote.casefold() for link in claims[ref].sources if link.relationship == "supports"):
                return fail("contact_binding_unproven")
        timing_sources = [sources[l.source_id] for ref in assessment.timing.claims for l in claims[ref].sources if l.relationship == "supports"]
        # Retrieval date alone cannot establish when a buying signal happened.
        if not any((s.event_at or s.published_at) and timedelta(0) <= now - (s.event_at or s.published_at) <= self.max_signal_age for s in timing_sources):
            return fail("timing_unknown")
        # Deliver source quotations, not an unchecked LLM sales summary.
        reason_claim = claims[assessment.reason_claims[0]]
        link = next(l for l in reason_claim.sources if l.relationship == "supports")
        return GateResult(decision=Decision.QUALIFIED, codes=("all_constraints_passed",), reason=link.quote, reason_source=sources[link.source_id].final_url)
