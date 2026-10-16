from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from evorove_lead.evidence import *
from evorove_lead.intelligence_store import *

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)

def bundle():
    source = SourceSnapshot(id="s", business_id="a", url="https://buyer.example/about", final_url="https://buyer.example/about", content="We need scheduling help", fetched_at=NOW, source_type="first_party", subject="buyer")
    claim = EvidenceClaim(id="c", subject="buyer", predicate="need", text="We need scheduling help", assertion_type="BUSINESS_CLAIM", confidence=.8, confidence_reason="published request", sources=(ClaimSource(source_id="s", quote="We need scheduling help"),))
    return EvidenceBundle(business_id="a", subject="buyer", sources=(source,), claims=(claim,))

def test_rejects_fabricated_source_quote_and_cross_tenant():
    data = bundle().model_dump()
    for mutation in ("source", "quote", "tenant"):
        bad = bundle().model_dump()
        if mutation == "source": bad["claims"][0]["sources"][0]["source_id"] = "invented"
        if mutation == "quote": bad["claims"][0]["sources"][0]["quote"] = "not present"
        if mutation == "tenant": bad["sources"][0]["business_id"] = "other"
        with pytest.raises(ValidationError): EvidenceBundle.model_validate(bad)

def test_quote_grounding_accepts_line_breaks_and_rejects_other_words():
    data = bundle().model_dump()
    data["sources"][0]["content"] = "We need\nscheduling help"
    data["sources"][0]["content_hash"] = ""
    data["claims"][0]["sources"][0]["quote"] = "We need scheduling help"
    EvidenceBundle.model_validate(data)
    data["claims"][0]["sources"][0]["quote"] = "We need billing help"
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(data)

def test_immutable_evidence_and_tenant_documents():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    store = IntelligenceStore(engine)
    store.save_evidence(bundle())
    store.save("a", "dna", "v1", {"version": 1})
    assert store.get("b", "dna", "v1") is None
    changed = bundle().model_copy(update={"sources": (bundle().sources[0].model_copy(update={"content": "changed"}),)})
    with pytest.raises(ValueError): store.save_evidence(changed)
    assert store.discover("a", "https://buyer.example/about", "h1")
    assert not store.discover("a", "https://buyer.example/team", "h2")
    assert store.discover("b", "https://buyer.example/team", "h1")
    with Session(engine) as session:
        assert len(session.scalars(select(CompanyDiscoveryRow)).all()) == 3
