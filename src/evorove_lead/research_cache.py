"""Tenant cache with explicit version keys and TTL; never a source of invented data."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import Session
from evorove_lead.sqlalchemy_models import Base, JSON_VALUE
from evorove_lead.evidence import digest, utcnow

class ResearchCacheRow(Base):
    __tablename__ = "research_cache"
    business_id = Column(String(128), primary_key=True)
    key = Column(String(64), primary_key=True)
    payload = Column(JSON_VALUE, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

class ResearchCache:
    def __init__(self, engine, clock=utcnow): self.engine, self.clock = engine, clock
    @staticmethod
    def key(*parts): return digest("\n".join(parts))
    def get(self, business_id, key):
        with Session(self.engine) as session:
            row = session.get(ResearchCacheRow, (business_id, key))
            if row is None: return None
            expiry = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
            return dict(row.payload) if expiry > self.clock() else None
    def put(self, business_id, key, payload, ttl=timedelta(days=1)):
        with Session(self.engine) as session, session.begin():
            session.merge(ResearchCacheRow(business_id=business_id, key=key, payload=payload, expires_at=self.clock() + ttl))
