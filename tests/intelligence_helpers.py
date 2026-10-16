"""Explicitly SYNTHETIC scripted research; never real-world evaluation data."""
import json
from datetime import datetime, timezone
from evorove_lead.llm_usage import CompletionReply
from evorove_lead.evidence import *
from evorove_lead.search_planner import ICPHypothesis
from evorove_lead.qualification import *
from evorove_lead.materials import DepositedMaterial
from evorove_lead.web_search import SearchHit
from evorove_lead.presence import PresenceRejected

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
SELLER = "Scheduling software for small businesses in the US. We help manage intake."
BUYER = "Jane Owner jane@buyer.example runs a small business in the US. We need scheduling software to manage intake after opening another location."

class Presence:
    def load(self, seed): return (DepositedMaterial(name="site", body=SELLER),)

class Search:
    def __init__(self): self.queries = []
    def search(self, query):
        self.queries.append(query)
        return (SearchHit(url="https://market.example/news" if query == "market demand evidence" else "https://buyer.example/news", snippet="published request"),)

def fetch(url, business_id, subject):
    if not url.endswith("/news"): raise PresenceRejected("not found")
    content = BUYER if "buyer.example" in url else "Small businesses expanding locations may need intake coordination. Alternatives include staffed reception."
    return SourceSnapshot(id=digest(url + subject), business_id=business_id, url=url, final_url=url, content=content, fetched_at=NOW, published_at=NOW, source_type="first_party", subject=subject)

def evidence(data, *, contact=False):
    sources = tuple(SourceSnapshot.model_validate(s) for s in data["sources"])
    source = sources[0]
    cid = digest(source.id + "claim")
    claim = EvidenceClaim(id=cid, subject=data.get("subject", source.subject), predicate="published_statement", text=source.content, assertion_type="BUSINESS_CLAIM", confidence=.8, confidence_reason="published statement", sources=(ClaimSource(source_id=source.id, quote=source.content),))
    person = ContactEvidence(person_name="Jane", role="Owner", identity="jane@buyer.example", subject=source.subject, name_claim=cid, role_claim=cid, address_claim=cid, relationship_claim=cid) if contact else None
    return EvidenceBundle(business_id=source.business_id, subject=source.subject, sources=sources, claims=(claim,), contact=person)

class Completion:
    def __init__(self, contradiction=False): self.calls = []; self.contradiction = contradiction
    def complete_bounded(self, *, system, user, max_tokens):
        data = json.loads(user)
        task = system.split("Task: ", 1)[1].split(". Schema:", 1)[0]
        self.calls.append(task)
        if task.startswith("Understand the business"):
            bundle = evidence(data)
            claim = bundle.claims[0].model_dump(mode="json")
            claim["slots"] = ["service", "geography", "customer_category"]
            result = {"business_id": data["business_id"], "site_url": data["site_url"], "version": data["version"], "service_country": "US", "evidence": {"business_id": bundle.business_id, "subject": bundle.subject, "claims": [claim]}}
        elif task.startswith("Plan research questions"):
            result = {"queries": ["market demand evidence"]}
        elif task.startswith("Interpret retrieved market"):
            bundle = evidence(data)
            claim = bundle.claims[0].model_dump(mode="json")
            claim["slots"] = ["segment", "demand_hypothesis", "buying_trigger", "competitive_alternative", "supporting"]
            result = {"unknowns": ["market size"], "confidence": .5, "priorities": ["published needs"], "evidence": {"business_id": bundle.business_id, "subject": bundle.subject, "claims": [claim]}}
        elif task.startswith("Name buyers who would say"):
            ref = data["dna"]["evidence"]["claims"][0]["id"]
            h = ICPHypothesis(segment="small businesses", buyer_role="owner", potential_problem="intake coordination", solution_mechanism="scheduling software may organize intake", signals=("expansion",), evidence_requirements=("explicit need",), disqualifiers=("closed",), geography="US", search_strategy=("request after expansion",), attractiveness_assumptions=("budget unknown",), confidence=.5, supporting_claims=(ref,))
            result = {"hypotheses": [h.model_dump(mode="json")]}
        elif task.startswith("Design targeted search"):
            result = {"queries": ["US business needs scheduling after expansion"], "rationale": "published need"}
        elif task.startswith("Extract claims") or task.startswith("Re-extract evidence"):
            result = evidence(data, contact=True).model_dump(mode="json")
        elif task.startswith("Assess six"):
            ref = data["evidence"]["claims"][0]["id"]
            dim = Dimension(score=.8, claims=(ref,), explanation="published request")
            result = QualificationAssessment(product_fit=dim, need_evidence=dim, timing=dim, evidence_reliability=dim, contact_relevance=dim, commercial_opportunity=Dimension(explanation="unknown purchasing capacity"), category_claim=ref, geography_claim=ref, geography_country="US", reason_claims=(ref,), supported_claims=(ref,), disqualifying_claims=(), unknowns=("budget",)).model_dump(mode="json")
        elif task.startswith("Independently challenge"):
            ref = data["evidence"]["claims"][0]["id"]
            result = AdversarialReview(supported_claims=(ref,), unsupported_claims=(), critical_contradictions=(ref,) if self.contradiction else (), disqualifying_claims=(), alternative_explanations=(), contact_appropriate=True, category_permitted=True, geography_compatible=True, need_demonstrated=True, explanation="explicit request from owner").model_dump(mode="json")
        elif task.startswith("Analyze failed research"):
            result = {"cause": "no sufficient evidence", "evidence": [data["history"][-1]["observation_id"]], "justified": False, "explanation": "stop without lowering the gate"}
        else: raise AssertionError(task)
        if task.startswith(("Extract claims", "Re-extract evidence")):
            result.pop("sources")
        return CompletionReply(json.dumps(result), 1000, 500)
