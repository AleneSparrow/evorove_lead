"""Typed evidence. A retrieved quote is not automatically an entailed conclusion."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from evorove_lead.presence import validate_public_http_url


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceSnapshot(StrictModel):
    id: str
    business_id: str
    url: str
    final_url: str
    content: str
    fetched_at: datetime
    published_at: datetime | None = None
    event_at: datetime | None = None
    status_code: int = 200
    public: bool = True
    source_type: Literal["first_party", "independent", "search_snippet", "owner_material"]
    material_name: str | None = None
    subject: str
    content_hash: str = ""

    @field_validator("url", "final_url")
    @classmethod
    def public_url(cls, value: str) -> str:
        return validate_public_http_url(value)

    @field_validator("fetched_at", "published_at", "event_at")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must have a timezone")
        return value

    @model_validator(mode="after")
    def hash_content(self):
        expected = digest(self.content)
        if self.content_hash and self.content_hash != expected:
            raise ValueError("content hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


def quote_is_in_source(quote: str, content: str) -> bool:
    """A line break in the page is still the same quote."""
    if quote in content:
        return True
    collapsed = re.sub(r"\s+", " ", quote).strip()
    if not collapsed:
        return False
    return collapsed in re.sub(r"\s+", " ", content)


class ClaimSource(StrictModel):
    source_id: str
    quote: str = Field(min_length=1)
    relationship: Literal["supports", "contradicts", "context"] = "supports"


ClaimId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$", description="Id of an evidence claim. Not the claim sentence.")]


class EvidenceClaim(StrictModel):
    id: str
    subject: str
    predicate: str
    text: str = Field(min_length=1)
    assertion_type: Literal["OBSERVED_FACT", "BUSINESS_CLAIM", "INFERENCE", "UNKNOWN"]
    confidence: float = Field(ge=0, le=1)
    confidence_reason: str
    sources: tuple[ClaimSource, ...] = ()


class ContactEvidence(StrictModel):
    person_name: str
    role: str
    identity: str
    subject: str
    name_claim: str
    role_claim: str
    address_claim: str
    relationship_claim: str
    generic: bool = False
    restricted: bool = False


class EvidenceDraft(StrictModel):
    """LLM output uses source IDs, never echoes or rewrites retrieved snapshots."""
    business_id: str
    subject: str
    subject_kind: Literal["company", "person"] = "company"
    claims: tuple[EvidenceClaim, ...]
    contact: ContactEvidence | None = None

    def bind(self, sources):
        return EvidenceBundle(**self.model_dump(), sources=tuple(sources))


class EvidenceBundle(StrictModel):
    business_id: str
    subject: str
    subject_kind: Literal["company", "person"] = "company"
    sources: tuple[SourceSnapshot, ...]
    claims: tuple[EvidenceClaim, ...]
    contact: ContactEvidence | None = None

    @model_validator(mode="after")
    def validate_references(self):
        sources = {s.id: s for s in self.sources}
        if len(sources) != len(self.sources) or len({c.id for c in self.claims}) != len(self.claims):
            raise ValueError("duplicate evidence identifiers")
        if any(s.business_id != self.business_id for s in self.sources):
            raise ValueError("cross-tenant source")
        for claim in self.claims:
            if claim.assertion_type in {"OBSERVED_FACT", "BUSINESS_CLAIM"} and not claim.sources:
                raise ValueError("factual claim requires sources")
            for link in claim.sources:
                source = sources.get(link.source_id)
                if source is None or not quote_is_in_source(link.quote, source.content):
                    raise ValueError("unknown source or ungrounded quote")
        if self.contact:
            ids = {c.id for c in self.claims}
            refs = (self.contact.name_claim, self.contact.address_claim, self.contact.relationship_claim)
            if self.subject_kind == "company" or self.contact.role_claim:
                refs = (*refs, self.contact.role_claim)
            if not set(refs) <= ids or self.contact.subject != self.subject:
                raise ValueError("contact references must belong to the subject")
        return self


def company_key(url: str) -> str:
    from evorove_lead.platforms import registrable_domain
    validate_public_http_url(url)
    return registrable_domain(urlsplit(url).hostname or "")
