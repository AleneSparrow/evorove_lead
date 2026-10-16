"""Versioned search-side Business DNA; never imports the sales Business DNA."""
from __future__ import annotations
from pydantic import Field
from typing import Literal
from evorove_lead.evidence import ClaimId, EvidenceBundle, EvidenceClaim, EvidenceDraft, StrictModel, digest


class BusinessDNA(StrictModel):
    schema_version: int = 1
    business_id: str
    version: str
    site_url: str
    services: tuple[str, ...] = Field(min_length=1)
    problems: tuple[str, ...] = ()
    value_proposition: tuple[str, ...] = ()
    business_model: tuple[str, ...] = ()
    geography: tuple[str, ...] = Field(min_length=1)
    service_country: Literal["US", "OTHER", "UNKNOWN"] = "UNKNOWN"
    service_localities: tuple[str, ...] = ()
    customer_categories: tuple[str, ...] = Field(min_length=1)
    positioning: tuple[str, ...] = ()
    differentiation: tuple[str, ...] = ()
    pricing: tuple[str, ...] = ()
    buying_constraints: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    must_not_claim: tuple[str, ...] = ("unverified price", "discount", "guarantee", "legal claim")
    unknowns: tuple[str, ...] = ()
    evidence: EvidenceBundle

    def validate_grounding(self) -> None:
        if self.evidence.business_id != self.business_id:
            raise ValueError("cross-tenant DNA")
        claims = {c.id: c for c in self.evidence.claims}
        for field in ("services", "problems", "value_proposition", "business_model", "geography", "customer_categories", "positioning", "differentiation", "pricing", "buying_constraints", "exclusions"):
            for ref in getattr(self, field):
                if ref not in claims:
                    raise ValueError(f"unknown DNA claim in {field}")
        import re
        area_quotes = " ".join(link.quote for ref in self.geography for link in claims[ref].sources if link.relationship == "supports")
        if self.service_country == "US" and not re.search(r"\bUS\b|\bUSA\b|United States|U\.S\.", area_quotes):
            raise ValueError("US service geography must be grounded")
        if any(place.casefold() not in area_quotes.casefold() for place in self.service_localities):
            raise ValueError("service locality must be grounded")
        for ref in (*self.services, *self.geography, *self.pricing):
            if claims[ref].assertion_type not in {"OBSERVED_FACT", "BUSINESS_CLAIM"}:
                raise ValueError("service, geography and price must be sourced")

    @staticmethod
    def version_for(content: str, prompt_version: str = "dna-2") -> str:
        return digest(prompt_version + "\n" + content)


DNA_SLOTS = {
    "service": "services",
    "problem": "problems",
    "value_proposition": "value_proposition",
    "business_model": "business_model",
    "geography": "geography",
    "customer_category": "customer_categories",
    "positioning": "positioning",
    "differentiation": "differentiation",
    "pricing": "pricing",
    "buying_constraint": "buying_constraints",
    "exclusion": "exclusions",
}


DnaSlot = Literal[
    "service", "problem", "value_proposition", "business_model", "geography",
    "customer_category", "positioning", "differentiation", "pricing",
    "buying_constraint", "exclusion",
]


class DnaClaim(EvidenceClaim):
    """One grounded statement, labeled with the DNA fields it supports."""

    id: ClaimId
    slots: tuple[DnaSlot, ...] = Field(min_length=1)


class DnaEvidenceDraft(EvidenceDraft):
    claims: tuple[DnaClaim, ...]

    def bind(self, sources):
        claims = tuple(EvidenceClaim.model_validate(claim.model_dump(exclude={"slots"})) for claim in self.claims)
        return EvidenceDraft(business_id=self.business_id, subject=self.subject, subject_kind=self.subject_kind, claims=claims, contact=self.contact).bind(sources)


class BusinessDNAModelDraft(StrictModel):
    """What the model fills in. Field lists of claim ids are assembled afterwards."""

    schema_version: int = 1
    business_id: str
    version: str
    site_url: str
    service_country: Literal["US", "OTHER", "UNKNOWN"] = "UNKNOWN"
    service_localities: tuple[str, ...] = ()
    must_not_claim: tuple[str, ...] = ("unverified price", "discount", "guarantee", "legal claim")
    unknowns: tuple[str, ...] = ()
    evidence: DnaEvidenceDraft

    def assemble(self, sources) -> BusinessDNA:
        grouped = {field: [] for field in DNA_SLOTS.values()}
        for claim in self.evidence.claims:
            for slot in dict.fromkeys(claim.slots):
                grouped[DNA_SLOTS[slot]].append(claim.id)
        return BusinessDNA(
            schema_version=self.schema_version,
            business_id=self.business_id,
            version=self.version,
            site_url=self.site_url,
            service_country=self.service_country,
            service_localities=self.service_localities,
            must_not_claim=self.must_not_claim,
            unknowns=self.unknowns,
            evidence=self.evidence.bind(sources),
            **{field: tuple(ids) for field, ids in grouped.items()},
        )
