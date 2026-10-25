"""Separate collect-once warehouse. No old research tables or CRM writes."""
from __future__ import annotations
from sqlalchemy import Boolean, Column, DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from evorove_lead.sqlalchemy_models import Base

class SourceCompany(Base):
    __tablename__ = 'companies'
    business_id = Column(String(128), primary_key=True)
    domain = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    site_url = Column(Text, nullable=False)
    suppressed = Column(Boolean, nullable=False, default=False)
    collected_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))

class SourcePerson(Base):
    __tablename__ = 'people'
    business_id = Column(String(128), primary_key=True)
    domain = Column(String(255), primary_key=True)
    name_key = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    role = Column(String(128), nullable=False, default='')
    suppressed = Column(Boolean, nullable=False, default=False)
    __table_args__ = (ForeignKeyConstraint(['business_id','domain'], ['companies.business_id','companies.domain']),)

class SourceContact(Base):
    __tablename__ = 'contacts'
    business_id = Column(String(128), primary_key=True)
    domain = Column(String(255), primary_key=True)
    email = Column(String(320), primary_key=True)
    name_key = Column(String(255), nullable=False)
    mx_verified = Column(Boolean, nullable=False)
    evidence_url = Column(Text, nullable=False)
    suppressed = Column(Boolean, nullable=False, default=False)
    __table_args__ = (ForeignKeyConstraint(['business_id','domain','name_key'], ['people.business_id','people.domain','people.name_key']),)

class SourceRawPage(Base):
    __tablename__ = 'raw_pages'
    business_id = Column(String(128), primary_key=True)
    domain = Column(String(255), primary_key=True)
    url = Column(Text, primary_key=True)
    text = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    sha256 = Column(String(64), nullable=False)
    __table_args__ = (ForeignKeyConstraint(['business_id','domain'], ['companies.business_id','companies.domain']),)

class SourceSignal(Base):
    __tablename__ = 'signals'
    business_id = Column(String(128), primary_key=True)
    source_url = Column(Text, primary_key=True)
    domain = Column(String(255), nullable=False)
    source = Column(String(32), nullable=False)
    signal_type = Column(String(64), nullable=False)
    signal_date = Column(DateTime(timezone=True), nullable=False)
    quote = Column(Text, nullable=False)
    source_text = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    context_only = Column(Boolean, nullable=False, default=False)
    is_advertisement = Column(Boolean, nullable=False, default=False)
    __table_args__ = (ForeignKeyConstraint(['business_id','domain'], ['companies.business_id','companies.domain']),)
