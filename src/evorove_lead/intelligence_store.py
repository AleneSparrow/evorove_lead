"""Tenant-scoped typed research documents plus normalized provenance edges.

Uses the existing database/metadata. JSON payloads are validated at the domain
boundary; sources and claims additionally have composite relational keys.
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKeyConstraint, select
from sqlalchemy.orm import Session
from evorove_lead.sqlalchemy_models import Base, JSON_VALUE
from evorove_lead.evidence import EvidenceBundle, company_key, digest, utcnow


class ResearchDocumentRow(Base):
    __tablename__ = "research_documents"
    business_id = Column(String(128), primary_key=True)
    kind = Column(String(40), primary_key=True)
    id = Column(String(128), primary_key=True)
    payload = Column(JSON_VALUE, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class ResearchSourceRow(Base):
    __tablename__ = "research_sources"
    business_id = Column(String(128), primary_key=True)
    id = Column(String(128), primary_key=True)
    payload = Column(JSON_VALUE, nullable=False)


class ResearchClaimRow(Base):
    __tablename__ = "research_claims"
    business_id = Column(String(128), primary_key=True)
    id = Column(String(128), primary_key=True)
    subject = Column(Text, nullable=False)
    payload = Column(JSON_VALUE, nullable=False)


class ClaimSourceRow(Base):
    __tablename__ = "research_claim_sources"
    business_id = Column(String(128), primary_key=True)
    claim_id = Column(String(128), primary_key=True)
    source_id = Column(String(128), primary_key=True)
    relationship = Column(String(16), primary_key=True)
    quote = Column(Text, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "claim_id"], ["research_claims.business_id", "research_claims.id"]),
        ForeignKeyConstraint(["business_id", "source_id"], ["research_sources.business_id", "research_sources.id"]),
    )


class ResearchCompanyRow(Base):
    __tablename__ = "research_companies"
    business_id = Column(String(128), primary_key=True)
    domain = Column(String(255), primary_key=True)
    first_seen = Column(DateTime(timezone=True), nullable=False)


class CompanyDiscoveryRow(Base):
    __tablename__ = "research_discoveries"
    business_id = Column(String(128), primary_key=True)
    domain = Column(String(255), primary_key=True)
    hypothesis_key = Column(String(128), primary_key=True)
    __table_args__ = (ForeignKeyConstraint(["business_id", "domain"], ["research_companies.business_id", "research_companies.domain"]),)


class IntelligenceStore:
    def __init__(self, engine):
        self.engine = engine

    def save(self, business_id: str, kind: str, identifier: str, payload: dict) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(ResearchDocumentRow(business_id=business_id, kind=kind, id=identifier, payload=payload, created_at=utcnow()))

    def get(self, business_id: str, kind: str, identifier: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.get(ResearchDocumentRow, (business_id, kind, identifier))
            return dict(row.payload) if row else None

    def list(self, business_id: str, kind: str) -> tuple[dict, ...]:
        with Session(self.engine) as session:
            return tuple(dict(r.payload) for r in session.scalars(select(ResearchDocumentRow).where(ResearchDocumentRow.business_id == business_id, ResearchDocumentRow.kind == kind).order_by(ResearchDocumentRow.created_at)))

    def save_evidence(self, bundle: EvidenceBundle) -> None:
        bundle = EvidenceBundle.model_validate(bundle.model_dump())
        with Session(self.engine) as session, session.begin():
            for source in bundle.sources:
                existing = session.get(ResearchSourceRow, (bundle.business_id, source.id))
                payload = source.model_dump(mode="json")
                if existing and existing.payload != payload:
                    raise ValueError("source snapshots are immutable")
                session.merge(ResearchSourceRow(business_id=bundle.business_id, id=source.id, payload=payload))
            session.flush()
            for claim in bundle.claims:
                payload = claim.model_dump(mode="json")
                existing = session.get(ResearchClaimRow, (bundle.business_id, claim.id))
                if existing and existing.payload != payload:
                    raise ValueError("claims are immutable")
                session.merge(ResearchClaimRow(business_id=bundle.business_id, id=claim.id, subject=claim.subject, payload=payload))
            session.flush()
            for claim in bundle.claims:
                for link in claim.sources:
                    session.merge(ClaimSourceRow(business_id=bundle.business_id, claim_id=claim.id, source_id=link.source_id, relationship=link.relationship, quote=link.quote))

    def discover(self, business_id: str, url: str, hypothesis_key: str) -> bool:
        """Persist every discovery edge; return whether the company is new."""
        domain = company_key(url)
        with Session(self.engine) as session, session.begin():
            fresh = session.get(ResearchCompanyRow, (business_id, domain)) is None
            if fresh:
                session.add(ResearchCompanyRow(business_id=business_id, domain=domain, first_seen=utcnow()))
                session.flush()
            session.merge(CompanyDiscoveryRow(business_id=business_id, domain=domain, hypothesis_key=hypothesis_key))
            return fresh
