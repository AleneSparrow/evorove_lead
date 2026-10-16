"""Offline gate replay and manually labelled quality evaluation. No network calls."""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path
from math import sqrt
from typing import Literal
from pydantic import Field, model_validator
from evorove_lead.evidence import StrictModel, EvidenceBundle
from evorove_lead.business_dna import BusinessDNA
from evorove_lead.qualification import QualificationAssessment, AdversarialReview, QualificationGate, Decision

class EvaluationCase(StrictModel):
    id: str
    profile: str
    buyer_type: Literal["B2B", "B2C"]
    split: Literal["development", "holdout"]
    provenance: Literal["real_public_snapshot", "synthetic"]
    company_key: str
    document_hashes: tuple[str, ...] = Field(min_length=1)
    labelled_by: str = Field(min_length=1)
    expected_qualified: bool
    supported_claim_ids: tuple[str, ...]
    valid_source_ids: tuple[str, ...]
    as_of: datetime
    dna: BusinessDNA
    bundle: EvidenceBundle
    assessment: QualificationAssessment
    review: AdversarialReview
    cost_microusd: int = Field(ge=0)
    baseline_qualified: bool | None = None
    baseline_version: str | None = None
    first_contact_delivered: bool | None = None
    positive_reply: bool | None = None
    converted: bool | None = None

    @model_validator(mode="after")
    def valid_labels(self):
        if self.baseline_qualified is not None and not self.baseline_version:
            raise ValueError("baseline result requires frozen baseline version")
        if self.as_of.tzinfo is None: raise ValueError("as_of needs timezone")
        return self


def wilson(successes, total, z=1.96):
    if total == 0: return None
    p = successes / total
    denom = 1 + z*z/total
    center = (p + z*z/(2*total)) / denom
    half = z * sqrt((p*(1-p) + z*z/(4*total))/total) / denom
    return [max(0, center-half), min(1, center+half)]


def validate_dataset(cases):
    if len({c.id for c in cases}) != len(cases): raise ValueError("duplicate case id")
    partitions = {}
    for case in cases:
        for key in ("company:" + case.company_key, *("doc:" + d for d in case.document_hashes)):
            if key in partitions and partitions[key] != case.split:
                raise ValueError("company/document leakage across splits")
            partitions[key] = case.split
    real = [c for c in cases if c.provenance == "real_public_snapshot"]
    ready = (len(real) >= 300 and len({c.profile for c in real}) >= 6 and
             sum(c.split == "development" for c in real) >= 180 and
             sum(c.split == "holdout" for c in real) >= 120 and
             {c.buyer_type for c in real} == {"B2B", "B2C"})
    return {"representative_dataset_available": ready, "real_cases": len(real), "synthetic_cases": len(cases)-len(real),
            "blocker": None if ready else "missing_representative_manually_labelled_300_case_dataset"}


def evaluate(cases, *, baseline=False):
    selected = []
    decisions = []
    for case in cases:
        if baseline:
            if case.baseline_qualified is None:
                return {"available": False, "blocker": "missing_frozen_baseline_predictions"}
            qualified = case.baseline_qualified
            decision = "QUALIFIED" if qualified else "REJECTED"
        else:
            gate = QualificationGate().evaluate(dna=case.dna, bundle=case.bundle, assessment=case.assessment, review=case.review, now=case.as_of)
            qualified, decision = gate.decision == Decision.QUALIFIED, gate.decision.value
        decisions.append(decision)
        if qualified: selected.append(case)
    tp = sum(c.expected_qualified for c in selected)
    positives = sum(c.expected_qualified for c in cases)
    total_claims = unsupported = total_sources = invalid_sources = 0
    for c in selected:
        a = c.assessment
        refs = {a.category_claim, a.geography_claim, *a.reason_claims,
                *(ref for d in (a.product_fit, a.need_evidence, a.timing, a.evidence_reliability, a.contact_relevance, a.commercial_opportunity) for ref in d.claims)}
        if c.bundle.contact:
            contact = c.bundle.contact
            refs.update(ref for ref in (contact.name_claim, contact.role_claim, contact.address_claim, contact.relationship_claim) if ref)
        total_claims += len(refs)
        unsupported += len(refs - set(c.supported_claim_ids))
        source_ids = {l.source_id for claim in c.bundle.claims if claim.id in refs for l in claim.sources if l.relationship == "supports"}
        total_sources += len(source_ids)
        invalid_sources += len(source_ids - set(c.valid_source_ids))
    contacts = {(c.dna.business_id, c.bundle.contact.identity.casefold()) for c in selected if c.bundle.contact}
    delivered = [c for c in selected if c.first_contact_delivered]
    def ratio(n, d): return n/d if d else None
    return {"available": True, "cases": len(cases), "qualified": len(selected), "true_positives": tp,
            "precision": ratio(tp, len(selected)), "precision_95_ci": wilson(tp, len(selected)),
            "recall": ratio(tp, positives), "recall_95_ci": wilson(tp, positives),
            "abstention_rate": ratio(sum(d in {"RESEARCH_MORE", "INSUFFICIENT_EVIDENCE"} for d in decisions), len(cases)),
            "unsupported_claim_rate": ratio(unsupported, total_claims),
            "source_validity": ratio(total_sources-invalid_sources, total_sources),
            "duplicate_rate": ratio(len(selected)-len(contacts), len(selected)),
            "cost_per_qualified_usd": ratio(sum(c.cost_microusd for c in cases)/1000000, len(selected)),
            "positive_reply_rate": ratio(sum(c.positive_reply is True for c in delivered), len(delivered)) if delivered and all(c.positive_reply is not None for c in delivered) else None,
            "conversion_rate": ratio(sum(c.converted is True for c in selected), len(selected)) if selected and all(c.converted is not None for c in selected) else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "holdout"), default="holdout")
    parser.add_argument("--schema", action="store_true")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(EvaluationCase.model_json_schema(), indent=2)); return 0
    if not args.dataset.is_file():
        print(json.dumps({"available": False, "blocker": "labelled_dataset_missing"})); return 2
    cases = [EvaluationCase.model_validate_json(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    validity = validate_dataset(cases)
    subset = [c for c in cases if c.split == args.split]
    print(json.dumps({"dataset": validity, "new": evaluate(subset), "baseline": evaluate(subset, baseline=True)}, indent=2))
    return 0 if validity["representative_dataset_available"] else 2

if __name__ == "__main__": raise SystemExit(main())
