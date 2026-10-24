from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
import pytest
from evorove_lead.evidence import *
from evorove_lead.qualification import *
from evorove_lead.opportunity_ranking import *

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)

def qualified_fixture():
    text = "Jane Owner jane@buyer.example needs intake help in the US."
    s = SourceSnapshot(id="s", business_id="a", url="https://buyer.example/news", final_url="https://buyer.example/news", content=text, fetched_at=NOW, published_at=NOW, source_type="first_party", subject="buyer.example")
    c = EvidenceClaim(id="c", subject="buyer.example", predicate="request", text=text, assertion_type="BUSINESS_CLAIM", confidence=.8, confidence_reason="published", sources=(ClaimSource(source_id="s", quote=text),))
    contact = ContactEvidence(person_name="Jane", role="Owner", identity="jane@buyer.example", subject="buyer.example", name_claim="c", role_claim="c", address_claim="c", relationship_claim="c")
    bundle = EvidenceBundle(business_id="a", subject="buyer.example", sources=(s,), claims=(c,), contact=contact)
    dim = Dimension(score=.9, claims=("c",), explanation="published request")
    a = QualificationAssessment(product_fit=dim, need_evidence=dim, timing=dim, evidence_reliability=dim, contact_relevance=dim, commercial_opportunity=Dimension(explanation="budget unknown"), category_claim="c", geography_claim="c", geography_country="US", reason_claims=("c",), supported_claims=("c",), disqualifying_claims=(), unknowns=("budget",))
    r = AdversarialReview(supported_claims=("c",), unsupported_claims=(), critical_contradictions=(), disqualifying_claims=(), alternative_explanations=(), contact_appropriate=True, category_permitted=True, geography_compatible=True, need_demonstrated=True, explanation="request supports need")
    dna = SimpleNamespace(business_id="a", site_url="https://seller.example", service_country="US", service_localities=(), validate_grounding=lambda: None)
    return dict(dna=dna, bundle=bundle, assessment=a, review=r, now=NOW)

def test_qualified_reason_is_exact_source_quote_and_rank_is_separate():
    args = qualified_fixture()
    result = QualificationGate().evaluate(**args)
    assert result.decision == Decision.QUALIFIED
    assert result.reason == args["bundle"].sources[0].content
    assert "commercial_opportunity" in CommercialOpportunityRanker().rank(result, args["assessment"]).unknown_dimensions

@pytest.mark.parametrize("failure", ["unsupported", "contradiction", "wrong_geo", "duplicate", "stale", "unknown_date", "wrong_contact", "missing"])
def test_scores_cannot_override_constraints(failure):
    args = qualified_fixture()
    if failure == "unsupported": args["review"] = args["review"].model_copy(update={"supported_claims": ()})
    if failure == "contradiction": args["review"] = args["review"].model_copy(update={"critical_contradictions": ("c",)})
    if failure == "wrong_geo": args["review"] = args["review"].model_copy(update={"geography_compatible": False})
    if failure == "duplicate": args["duplicate"] = True
    if failure in {"stale", "unknown_date"}:
        source = args["bundle"].sources[0].model_copy(update={"fetched_at": NOW - timedelta(days=100)} if failure == "stale" else {"published_at": None})
        args["bundle"] = args["bundle"].model_copy(update={"sources": (source,)})
    if failure == "wrong_contact": args["bundle"] = args["bundle"].model_copy(update={"contact": args["bundle"].contact.model_copy(update={"person_name": "Invented"})})
    if failure == "missing": args["bundle"] = None
    result = QualificationGate().evaluate(**args)
    assert result.decision != Decision.QUALIFIED
    with pytest.raises(ValueError): CommercialOpportunityRanker().rank(result, args["assessment"])

def test_person_buyer_does_not_require_fabricated_business_role():
    args = qualified_fixture()
    contact = args["bundle"].contact.model_copy(update={"role": "", "role_claim": ""})
    args["bundle"] = args["bundle"].model_copy(update={"subject_kind": "person", "contact": contact})
    assert QualificationGate().evaluate(**args).decision == Decision.QUALIFIED


def test_company_role_is_still_mandatory():
    args = qualified_fixture()
    contact = args["bundle"].contact.model_copy(update={"role": "", "role_claim": ""})
    args["bundle"] = args["bundle"].model_copy(update={"contact": contact})
    assert QualificationGate().evaluate(**args).decision != Decision.QUALIFIED


def test_empty_client_geography_does_not_relax_candidate_us_gate():
    args = qualified_fixture()
    assert args["dna"].service_country == "US"
    assert QualificationGate().evaluate(**args).decision == Decision.QUALIFIED
    args["dna"] = SimpleNamespace(
        business_id="a", site_url="https://seller.example", service_country="OTHER",
        service_localities=(), validate_grounding=lambda: None,
    )
    other = QualificationGate().evaluate(**args)
    assert other.decision != Decision.QUALIFIED
    assert other.codes == ("country_not_confirmed",)
    args = qualified_fixture()
    claim = args["bundle"].claims[0].model_copy(update={
        "sources": (ClaimSource(source_id="s", quote="Jane Owner jane@buyer.example needs intake help"),),
    })
    args["bundle"] = args["bundle"].model_copy(update={"claims": (claim,)})
    ungrounded = QualificationGate().evaluate(**args)
    assert ungrounded.decision != Decision.QUALIFIED
    assert ungrounded.codes == ("country_not_grounded",)
