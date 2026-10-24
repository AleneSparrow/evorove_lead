"""Atomic admission + existing CRM outbox, with durable contact deduplication."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import Column, String, DateTime, ForeignKeyConstraint, select, update, delete, func, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from evorove_lead.sqlalchemy_models import Base, CrmDeliveryRow
from evorove_lead.sqlalchemy_warehouse import _candidate_to_row
from evorove_lead.warehouse import new_id
from evorove_lead.evidence import utcnow, digest

class AdmissionRow(Base):
    __tablename__ = "research_admissions"
    business_id = Column(String(128), primary_key=True)
    contact_key = Column(String(64), primary_key=True)
    candidate_id = Column(String(128), nullable=False)
    hypothesis_key = Column(String(128), nullable=False)
    cohort_id = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (ForeignKeyConstraint(["business_id", "candidate_id"], ["candidates.business_id", "candidates.id"]),)

class CandidateClaimRow(Base):
    __tablename__ = "research_candidate_claims"
    business_id = Column(String(128), primary_key=True)
    candidate_id = Column(String(128), primary_key=True)
    claim_id = Column(String(128), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "candidate_id"], ["candidates.business_id", "candidates.id"]),
        ForeignKeyConstraint(["business_id", "claim_id"], ["research_claims.business_id", "research_claims.id"]),
    )

class RunLeaseRow(Base):
    __tablename__ = "research_run_leases"
    business_id = Column(String(128), primary_key=True)
    run_id = Column(String(128), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

class ResearchDelivery:
    def __init__(self, engine): self.engine = engine
    def seen(self, business_id, identity):
        from evorove_lead.sqlalchemy_models import CandidateRow
        with Session(self.engine) as session:
            if session.get(AdmissionRow, (business_id, digest(identity.strip().casefold()))): return True
            return session.scalar(select(CandidateRow.id).where(CandidateRow.business_id == business_id, CandidateRow.decision == "cold", or_(func.lower(CandidateRow.identity) == identity.strip().casefold(), func.lower(CandidateRow.email) == identity.strip().casefold(), CandidateRow.phone == identity.strip())).limit(1)) is not None

    def admit(self, record, payload, hypothesis_key, cohort_id, *, claim_ids=()):
        """Concurrent duplicate: no second candidate or delivery. Other DB errors propagate."""
        key = digest(record.identity.strip().casefold())
        try:
            with Session(self.engine) as session, session.begin():
                session.add(_candidate_to_row(record))
                session.flush()
                session.add(AdmissionRow(business_id=record.business_id, contact_key=key, candidate_id=record.id, hypothesis_key=hypothesis_key, cohort_id=cohort_id, created_at=record.decided_at))
                session.flush()
                for claim_id in claim_ids:
                    session.add(CandidateClaimRow(business_id=record.business_id, candidate_id=record.id, claim_id=claim_id))
                session.add(CrmDeliveryRow(id=new_id("delivery"), business_id=record.business_id, touch_id=payload["touch_id"], payload=payload, attempts=0, last_error="", created_at=record.decided_at, updated_at=record.decided_at))
            return True
        except IntegrityError:
            with Session(self.engine) as session:
                if session.get(AdmissionRow, (record.business_id, key)): return False
            raise

