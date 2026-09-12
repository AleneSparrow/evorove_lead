"""ORM tables for cycle 1's own analysis warehouse.

This is the "two databases, not one pile" split from
`docs/cycle-1-contract.md`: raw traces, rejections, and scores live here,
tenant-scoped by `business_id`. Only a person who passes re-analysis ever
reaches CRM Cold — that board is a separate Postgres in `evorove-crm` and
these tables never feed it directly.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase

JSON_VALUE = postgresql.JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite")


class Base(DeclarativeBase):
    pass


class BriefRow(Base):
    """A snapshot of `OfferUnderstanding` at a point in time. Never overwritten."""

    __tablename__ = "briefs"

    id = Column(String(128), primary_key=True)
    business_id = Column(String(128), nullable=False)
    what_we_sell = Column(JSON_VALUE, nullable=False)
    who_may_fit = Column(JSON_VALUE, nullable=False)
    commercial_claims = Column(JSON_VALUE, nullable=False)
    must_not_promise = Column(JSON_VALUE, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_briefs_business_id_id"),
    )


class HypothesisRow(Base):
    """A testable (audience, channel, query) triple derived from a brief."""

    __tablename__ = "hypotheses"

    id = Column(String(128), primary_key=True)
    business_id = Column(String(128), nullable=False)
    brief_id = Column(String(128), nullable=False)
    audience_segment = Column(String(255), nullable=False)
    channel = Column(String(64), nullable=False)
    query_template = Column(Text, nullable=False)
    intent_trigger = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    fit_score = Column(Float, nullable=False, default=0.0)
    evidence_score = Column(Float, nullable=False, default=0.0)
    reach_estimate = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)
    # Phase 4: filled in by reweight_hypotheses.py, not by the engine.
    # accept_rate = share of candidates this hypothesis produced that
    # reached Cold; close_rate = share of those Cold candidates whose case
    # later reached Done. query_budget is how many probe rounds the next
    # run should spend on this hypothesis (0 once paused).
    accept_rate = Column(Float, nullable=False, default=0.0)
    close_rate = Column(Float, nullable=False, default=0.0)
    query_budget = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_hypotheses_business_id_id"),
        ForeignKeyConstraint(
            ["business_id", "brief_id"],
            ["briefs.business_id", "briefs.id"],
            name="fk_hypotheses_tenant_brief",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('live','dead','paused')", name="ck_hypotheses_known_status"
        ),
        CheckConstraint("query_budget >= 0", name="ck_hypotheses_budget_nonnegative"),
    )


class TraceRow(Base):
    """One raw public-web trace a hypothesis's query turned up."""

    __tablename__ = "traces"

    id = Column(String(128), primary_key=True)
    business_id = Column(String(128), nullable=False)
    hypothesis_id = Column(String(128), nullable=False)
    url = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    raw_text = Column(Text, nullable=False)
    query_used = Column(Text, nullable=False)
    language = Column(String(16))
    geo_hint = Column(String(255))
    source_channel = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_traces_business_id_id"),
        ForeignKeyConstraint(
            ["business_id", "hypothesis_id"],
            ["hypotheses.business_id", "hypotheses.id"],
            name="fk_traces_tenant_hypothesis",
            ondelete="CASCADE",
        ),
    )


class RejectedTraceRow(Base):
    """A trace that failed topic/geo/openness/freshness. Kept for tuning, not Cold."""

    __tablename__ = "rejected_traces"

    id = Column(String(128), primary_key=True)
    business_id = Column(String(128), nullable=False)
    trace_id = Column(String(128), nullable=True)
    reason = Column(Text, nullable=False)
    rejected_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_rejected_traces_business_id_id"),
    )


class CandidateRow(Base):
    """Warehouse-side scored person extracted from a trace: cold or rejected."""

    __tablename__ = "candidates"

    id = Column(String(128), primary_key=True)
    business_id = Column(String(128), nullable=False)
    hypothesis_id = Column(String(128), nullable=False)
    trace_id = Column(String(128), nullable=True)
    identity = Column(Text, nullable=False)
    email = Column(String(320))
    phone = Column(String(64))
    channel = Column(String(32), nullable=False)
    reason = Column(Text, nullable=False)
    reason_source = Column(Text, nullable=False)
    fit = Column(Float, nullable=False)
    evidence = Column(Float, nullable=False)
    addressable = Column(Boolean, nullable=False)
    decision = Column(String(16), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_candidates_business_id_id"),
        ForeignKeyConstraint(
            ["business_id", "hypothesis_id"],
            ["hypotheses.business_id", "hypotheses.id"],
            name="fk_candidates_tenant_hypothesis",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "decision IN ('cold','rejected')", name="ck_candidates_known_decision"
        ),
    )


class HypothesisOutcomeRow(Base):
    """Non-PII outcome event fed back from cycle 2/3: hypothesis_id -> outcome."""

    __tablename__ = "hypothesis_outcomes"

    id = Column(String(128), primary_key=True)
    business_id = Column(String(128), nullable=False)
    hypothesis_id = Column(String(128), nullable=False)
    case_id = Column(String(128), nullable=False)
    outcome = Column(String(16), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "hypothesis_id"],
            ["hypotheses.business_id", "hypotheses.id"],
            name="fk_hypothesis_outcomes_tenant_hypothesis",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "outcome IN ('done','dropped','offer_made','in_progress')",
            name="ck_hypothesis_outcomes_known_outcome",
        ),
    )


class HypothesisPatternLibraryRow(Base):
    """System-wide, NOT tenant-scoped: abstract patterns only.

    `business_archetype -> channel_family -> query_pattern ->
    observed_close_rate_band`. `query_pattern` is a category (an
    `IntentTrigger.kind`, e.g. "need_statement"), never a hypothesis's
    literal `query_template` text -- that text can quote the owner's own
    service wording and would identify the business. No contact, trace
    text, or business/person identifier belongs in this table, ever.
    """

    __tablename__ = "hypothesis_pattern_library"

    id = Column(String(128), primary_key=True)
    business_archetype = Column(String(128), nullable=False)
    channel_family = Column(String(64), nullable=False)
    query_pattern = Column(String(64), nullable=False)
    observed_close_rate_band = Column(String(16), nullable=False)
    sample_size = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "business_archetype",
            "channel_family",
            "query_pattern",
            name="uq_hypothesis_pattern_library_key",
        ),
        CheckConstraint(
            "observed_close_rate_band IN ('low','medium','high')",
            name="ck_hypothesis_pattern_library_known_band",
        ),
        CheckConstraint("sample_size >= 0", name="ck_hypothesis_pattern_library_sample_nonnegative"),
    )
