"""SQLAlchemy implementation of the analysis-warehouse port.

The engine and re-analysis code depend on `warehouse.AnalysisWarehouse`,
not on this module. Keep the ORM here so the ports stay swappable.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from evorove_lead.crm_touch import (
    CrmDeliveryRecord,
    CrmDeliveryStore,
    NullCrmDeliveryStore,
    flush_deliveries,
    http_sink_from_env,
)
from evorove_lead.sqlalchemy_models import (
    Base,
    BriefRow,
    CandidateRow,
    CrmDeliveryRow,
    HypothesisOutcomeRow,
    HypothesisRow,
    LeadFeedbackRow,
    LeadQualificationRow,
    LlmUsageRow,
    RejectedTraceRow,
    TraceRow,
)
from evorove_lead.warehouse import (
    AnalysisWarehouse,
    BriefRecord,
    CandidateRecord,
    FeedbackRecord,
    HypothesisOutcomeRecord,
    HypothesisRecord,
    LlmUsageRecord,
    NullAnalysisWarehouse,
    QualificationRecord,
    RejectedTraceRecord,
    TraceRecord,
    new_id,
)


def database_url_from_environment() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return database_url


def engine_from_env():
    return create_engine(database_url_from_environment(), future=True)


def _brief_to_row(record: BriefRecord) -> BriefRow:
    return BriefRow(
        id=record.id,
        business_id=record.business_id,
        what_we_sell=list(record.what_we_sell),
        who_may_fit=list(record.who_may_fit),
        commercial_claims=list(record.commercial_claims),
        must_not_promise=list(record.must_not_promise),
        created_at=record.created_at,
        business_archetype=record.business_archetype,
        marketing_analysis=record.marketing_analysis,
    )


def _brief_from_row(row: BriefRow) -> BriefRecord:
    return BriefRecord(
        id=row.id,
        business_id=row.business_id,
        what_we_sell=tuple(row.what_we_sell or ()),
        who_may_fit=tuple(row.who_may_fit or ()),
        commercial_claims=tuple(row.commercial_claims or ()),
        must_not_promise=tuple(row.must_not_promise or ()),
        business_archetype=row.business_archetype or "",
        created_at=row.created_at,
        marketing_analysis=row.marketing_analysis,
    )


def _hypothesis_to_row(record: HypothesisRecord) -> HypothesisRow:
    return HypothesisRow(
        id=record.id,
        business_id=record.business_id,
        brief_id=record.brief_id,
        audience_segment=record.audience_segment,
        channel=record.channel,
        query_template=record.query_template,
        intent_trigger=record.intent_trigger,
        status=record.status,
        fit_score=record.fit_score,
        evidence_score=record.evidence_score,
        reach_estimate=record.reach_estimate,
        created_at=record.created_at,
        accept_rate=record.accept_rate,
        close_rate=record.close_rate,
        query_budget=record.query_budget,
        audience_source=record.audience_source,
        evidence_quote=record.evidence_quote,
    )


def _hypothesis_from_row(row: HypothesisRow) -> HypothesisRecord:
    return HypothesisRecord(
        id=row.id,
        business_id=row.business_id,
        brief_id=row.brief_id,
        audience_segment=row.audience_segment,
        channel=row.channel,
        query_template=row.query_template,
        intent_trigger=row.intent_trigger,
        status=row.status,
        fit_score=row.fit_score,
        evidence_score=row.evidence_score,
        reach_estimate=row.reach_estimate,
        created_at=row.created_at,
        accept_rate=row.accept_rate,
        close_rate=row.close_rate,
        query_budget=row.query_budget,
        audience_source=row.audience_source or "literal",
        evidence_quote=row.evidence_quote or "",
    )


def _trace_to_row(record: TraceRecord) -> TraceRow:
    return TraceRow(
        id=record.id,
        business_id=record.business_id,
        hypothesis_id=record.hypothesis_id,
        url=record.url,
        fetched_at=record.fetched_at,
        raw_text=record.raw_text,
        query_used=record.query_used,
        language=record.language or None,
        geo_hint=record.geo_hint or None,
        source_channel=record.source_channel,
    )


def _trace_from_row(row: TraceRow) -> TraceRecord:
    return TraceRecord(
        id=row.id,
        business_id=row.business_id,
        hypothesis_id=row.hypothesis_id,
        url=row.url,
        fetched_at=row.fetched_at,
        raw_text=row.raw_text,
        query_used=row.query_used,
        source_channel=row.source_channel,
        language=row.language or "",
        geo_hint=row.geo_hint or "",
    )


def _rejected_trace_to_row(record: RejectedTraceRecord) -> RejectedTraceRow:
    return RejectedTraceRow(
        id=record.id,
        business_id=record.business_id,
        trace_id=record.trace_id or None,
        reason=record.reason,
        rejected_at=record.rejected_at,
    )


def _rejected_trace_from_row(row: RejectedTraceRow) -> RejectedTraceRecord:
    return RejectedTraceRecord(
        id=row.id,
        business_id=row.business_id,
        trace_id=row.trace_id or "",
        reason=row.reason,
        rejected_at=row.rejected_at,
    )


def _candidate_to_row(record: CandidateRecord) -> CandidateRow:
    return CandidateRow(
        id=record.id,
        business_id=record.business_id,
        hypothesis_id=record.hypothesis_id,
        trace_id=record.trace_id or None,
        identity=record.identity,
        email=record.email or None,
        phone=record.phone or None,
        rejection_reason=record.rejection_reason,
        observed_pain=record.observed_pain,
        pain_evidence_excerpt=record.pain_evidence_excerpt,
        pain_evidence_source=record.pain_evidence_source,
        channel=record.channel,
        reason=record.reason,
        reason_source=record.reason_source,
        fit=record.fit,
        evidence=record.evidence,
        addressable=record.addressable,
        decision=record.decision,
        decided_at=record.decided_at,
    )


def _candidate_from_row(row: CandidateRow) -> CandidateRecord:
    return CandidateRecord(
        id=row.id,
        business_id=row.business_id,
        hypothesis_id=row.hypothesis_id,
        identity=row.identity,
        channel=row.channel,
        reason=row.reason,
        reason_source=row.reason_source,
        fit=row.fit,
        evidence=row.evidence,
        addressable=row.addressable,
        decision=row.decision,
        decided_at=row.decided_at,
        trace_id=row.trace_id or "",
        email=row.email or "",
        phone=row.phone or "",
        rejection_reason=row.rejection_reason or "",
        observed_pain=row.observed_pain or "",
        pain_evidence_excerpt=row.pain_evidence_excerpt or "",
        pain_evidence_source=row.pain_evidence_source or "",
    )


def _outcome_to_row(record: HypothesisOutcomeRecord) -> HypothesisOutcomeRow:
    return HypothesisOutcomeRow(
        id=record.id,
        business_id=record.business_id,
        hypothesis_id=record.hypothesis_id,
        case_id=record.case_id,
        outcome=record.outcome,
        recorded_at=record.recorded_at,
    )


def _outcome_from_row(row: HypothesisOutcomeRow) -> HypothesisOutcomeRecord:
    return HypothesisOutcomeRecord(
        id=row.id,
        business_id=row.business_id,
        hypothesis_id=row.hypothesis_id,
        case_id=row.case_id,
        outcome=row.outcome,
        recorded_at=row.recorded_at,
    )


def _qualification_to_row(record: QualificationRecord) -> LeadQualificationRow:
    return LeadQualificationRow(
        id=record.id,
        business_id=record.business_id,
        hypothesis_id=record.hypothesis_id,
        trace_id=record.trace_id,
        source_url=record.source_url,
        content_sha256=record.content_sha256,
        query_template=record.query_template,
        segment=record.segment,
        intent_class=record.intent_class,
        entity_type=record.entity_type,
        problem=record.problem or None,
        icp_match=record.icp_match,
        evidence_quote=record.evidence_quote or None,
        evidence_verified=record.evidence_verified,
        publication_date=record.publication_date or None,
        confidence=record.confidence,
        commercial_relevance=record.commercial_relevance,
        recommended_action=record.recommended_action,
        reasoning_summary=record.reasoning_summary,
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cost_usd_micros=record.cost_usd_micros,
        cache_hit=record.cache_hit,
        blocked_reason=record.blocked_reason,
        created_at=record.created_at,
    )


def _qualification_from_row(row: LeadQualificationRow) -> QualificationRecord:
    return QualificationRecord(
        id=row.id,
        business_id=row.business_id,
        hypothesis_id=row.hypothesis_id or "",
        trace_id=row.trace_id or "",
        source_url=row.source_url,
        content_sha256=row.content_sha256,
        query_template=row.query_template or "",
        segment=row.segment,
        intent_class=row.intent_class,
        entity_type=row.entity_type or "unknown",
        problem=row.problem or "",
        icp_match=bool(row.icp_match),
        evidence_quote=row.evidence_quote or "",
        evidence_verified=bool(row.evidence_verified),
        publication_date=row.publication_date or "",
        confidence=row.confidence or 0.0,
        commercial_relevance=row.commercial_relevance or "unknown",
        recommended_action=row.recommended_action or "",
        reasoning_summary=row.reasoning_summary or "",
        model=row.model or "",
        input_tokens=row.input_tokens or 0,
        output_tokens=row.output_tokens or 0,
        cost_usd_micros=row.cost_usd_micros or 0,
        cache_hit=bool(row.cache_hit),
        blocked_reason=row.blocked_reason or "",
        created_at=row.created_at,
    )


def _feedback_to_row(record: FeedbackRecord) -> LeadFeedbackRow:
    return LeadFeedbackRow(
        id=record.id,
        business_id=record.business_id,
        qualification_id=record.qualification_id,
        hypothesis_id=record.hypothesis_id,
        query_template=record.query_template,
        kind=record.kind,
        budget_delta=record.budget_delta,
        created_at=record.created_at,
    )


def _feedback_from_row(row: LeadFeedbackRow) -> FeedbackRecord:
    return FeedbackRecord(
        id=row.id,
        business_id=row.business_id,
        qualification_id=row.qualification_id,
        hypothesis_id=row.hypothesis_id or "",
        query_template=row.query_template or "",
        kind=row.kind,
        budget_delta=row.budget_delta,
        created_at=row.created_at,
    )


def _usage_to_row(record: LlmUsageRecord) -> LlmUsageRow:
    return LlmUsageRow(
        id=record.id,
        business_id=record.business_id,
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cost_usd_micros=record.cost_usd_micros,
        cache_hit=record.cache_hit,
        blocked_reason=record.blocked_reason,
        created_at=record.created_at,
    )


class SqlAlchemyAnalysisWarehouse:
    """Tenant-scoped analysis warehouse backed by Postgres (or SQLite in tests)."""

    def __init__(self, engine) -> None:
        self.database_engine = engine
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )

    def save_brief(self, record: BriefRecord) -> None:
        with self._session_factory() as session:
            session.merge(_brief_to_row(record))
            session.commit()

    def list_briefs(self, business_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(BriefRow)
                .where(BriefRow.business_id == business_id)
                .order_by(BriefRow.created_at)
            )
            return tuple(_brief_from_row(row) for row in rows)

    def save_hypothesis(self, record: HypothesisRecord) -> None:
        with self._session_factory() as session:
            session.merge(_hypothesis_to_row(record))
            session.commit()

    def list_hypotheses(self, business_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(HypothesisRow)
                .where(HypothesisRow.business_id == business_id)
                .order_by(HypothesisRow.created_at)
            )
            return tuple(_hypothesis_from_row(row) for row in rows)

    def save_trace(self, record: TraceRecord) -> None:
        with self._session_factory() as session:
            session.merge(_trace_to_row(record))
            session.commit()

    def list_traces(self, business_id: str, hypothesis_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(TraceRow)
                .where(
                    TraceRow.business_id == business_id,
                    TraceRow.hypothesis_id == hypothesis_id,
                )
                .order_by(TraceRow.fetched_at)
            )
            return tuple(_trace_from_row(row) for row in rows)

    def save_rejected_trace(self, record: RejectedTraceRecord) -> None:
        with self._session_factory() as session:
            session.merge(_rejected_trace_to_row(record))
            session.commit()

    def list_rejected_traces(self, business_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(RejectedTraceRow)
                .where(RejectedTraceRow.business_id == business_id)
                .order_by(RejectedTraceRow.rejected_at)
            )
            return tuple(_rejected_trace_from_row(row) for row in rows)

    def save_candidate(self, record: CandidateRecord) -> None:
        with self._session_factory() as session:
            session.merge(_candidate_to_row(record))
            session.commit()

    def list_candidates(self, business_id: str, hypothesis_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(CandidateRow)
                .where(
                    CandidateRow.business_id == business_id,
                    CandidateRow.hypothesis_id == hypothesis_id,
                )
                .order_by(CandidateRow.decided_at)
            )
            return tuple(_candidate_from_row(row) for row in rows)

    def accepted_identities(self, business_id: str):
        with self._session_factory() as session:
            return tuple(
                session.scalars(
                    select(CandidateRow.identity).where(
                        CandidateRow.business_id == business_id, CandidateRow.decision == "cold"
                    )
                )
            )

    def save_hypothesis_outcome(self, record: HypothesisOutcomeRecord) -> None:
        with self._session_factory() as session:
            session.merge(_outcome_to_row(record))
            session.commit()

    def list_hypothesis_outcomes(self, business_id: str, hypothesis_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(HypothesisOutcomeRow)
                .where(
                    HypothesisOutcomeRow.business_id == business_id,
                    HypothesisOutcomeRow.hypothesis_id == hypothesis_id,
                )
                .order_by(HypothesisOutcomeRow.recorded_at)
            )
            return tuple(_outcome_from_row(row) for row in rows)

    def save_qualification(self, record: QualificationRecord) -> QualificationRecord:
        with self._session_factory() as session:
            existing = session.scalar(
                select(LeadQualificationRow).where(
                    LeadQualificationRow.business_id == record.business_id,
                    LeadQualificationRow.source_url == record.source_url,
                    LeadQualificationRow.content_sha256 == record.content_sha256,
                )
            )
            if existing is not None:
                return _qualification_from_row(existing)
            session.add(_qualification_to_row(record))
            try:
                session.commit()
            except Exception:
                session.rollback()
                existing = session.scalar(
                    select(LeadQualificationRow).where(
                        LeadQualificationRow.business_id == record.business_id,
                        LeadQualificationRow.source_url == record.source_url,
                        LeadQualificationRow.content_sha256 == record.content_sha256,
                    )
                )
                if existing is not None:
                    return _qualification_from_row(existing)
                raise
        return record

    def list_qualifications(self, business_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(LeadQualificationRow)
                .where(LeadQualificationRow.business_id == business_id)
                .order_by(LeadQualificationRow.created_at)
            )
            return tuple(_qualification_from_row(row) for row in rows)

    def get_qualification(self, business_id: str, qualification_id: str):
        with self._session_factory() as session:
            row = session.scalar(
                select(LeadQualificationRow).where(
                    LeadQualificationRow.business_id == business_id,
                    LeadQualificationRow.id == qualification_id,
                )
            )
            return _qualification_from_row(row) if row is not None else None

    def find_qualification_by_sha(self, business_id: str, content_sha256: str):
        with self._session_factory() as session:
            row = session.scalar(
                select(LeadQualificationRow).where(
                    LeadQualificationRow.business_id == business_id,
                    LeadQualificationRow.content_sha256 == content_sha256,
                )
            )
            return _qualification_from_row(row) if row is not None else None

    def save_feedback(self, record: FeedbackRecord) -> None:
        with self._session_factory() as session:
            session.merge(_feedback_to_row(record))
            session.commit()

    def list_feedback(self, business_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(LeadFeedbackRow)
                .where(LeadFeedbackRow.business_id == business_id)
                .order_by(LeadFeedbackRow.created_at)
            )
            return tuple(_feedback_from_row(row) for row in rows)

    def suppressed_queries(self, business_id: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(LeadFeedbackRow.query_template).where(
                    LeadFeedbackRow.business_id == business_id,
                    LeadFeedbackRow.kind.in_(("irrelevant", "incorrect_icp", "outdated")),
                    LeadFeedbackRow.query_template != "",
                )
            )
            return tuple(rows)

    def save_llm_usage(self, record: LlmUsageRecord) -> None:
        with self._session_factory() as session:
            session.merge(_usage_to_row(record))
            session.commit()

    def spent_micros(self, business_id: str | None = None) -> int:
        with self._session_factory() as session:
            query = select(func.coalesce(func.sum(LlmUsageRow.cost_usd_micros), 0))
            if business_id is not None:
                query = query.where(LlmUsageRow.business_id == business_id)
            return int(session.scalar(query) or 0)


def create_all(engine) -> None:
    """Test/dev convenience: create tables without Alembic. Prod uses migrations."""

    Base.metadata.create_all(engine)


def warehouse_from_env() -> AnalysisWarehouse:
    """No `DATABASE_URL` -> `NullAnalysisWarehouse`, same fallback shape as `crm_touch.sink_from_env`."""

    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return NullAnalysisWarehouse()
    return SqlAlchemyAnalysisWarehouse(create_engine(database_url, future=True))


def _delivery_from_row(row: CrmDeliveryRow) -> CrmDeliveryRecord:
    return CrmDeliveryRecord(
        id=row.id,
        business_id=row.business_id,
        touch_id=row.touch_id,
        payload=dict(row.payload or {}),
        attempts=row.attempts,
        last_error=row.last_error or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyCrmDeliveryStore:
    """Durable CRM delivery queue backed by Postgres (or SQLite in tests)."""

    def __init__(self, engine) -> None:
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )

    def record_failure(
        self,
        business_id: str,
        touch_id: str,
        payload: dict[str, object],
        error: str,
        now: datetime,
    ) -> None:
        with self._session_factory() as session:
            row = session.scalars(
                select(CrmDeliveryRow).where(
                    CrmDeliveryRow.business_id == business_id,
                    CrmDeliveryRow.touch_id == touch_id,
                )
            ).first()
            if row is None:
                session.add(
                    CrmDeliveryRow(
                        id=new_id("delivery"),
                        business_id=business_id,
                        touch_id=touch_id,
                        payload=dict(payload),
                        attempts=1,
                        last_error=error,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                row.attempts = row.attempts + 1
                row.last_error = error
                row.payload = dict(payload)
                row.updated_at = now
            session.commit()

    def pending(self):
        with self._session_factory() as session:
            rows = session.scalars(select(CrmDeliveryRow).order_by(CrmDeliveryRow.created_at))
            return tuple(_delivery_from_row(row) for row in rows)

    def remove(self, business_id: str, touch_id: str) -> None:
        with self._session_factory() as session:
            row = session.scalars(
                select(CrmDeliveryRow).where(
                    CrmDeliveryRow.business_id == business_id,
                    CrmDeliveryRow.touch_id == touch_id,
                )
            ).first()
            if row is not None:
                session.delete(row)
                session.commit()

    def pending_count(self) -> int:
        with self._session_factory() as session:
            return session.scalar(select(func.count()).select_from(CrmDeliveryRow)) or 0


def crm_delivery_store_from_env() -> CrmDeliveryStore:
    """No `DATABASE_URL` -> `NullCrmDeliveryStore`, same fallback shape as `warehouse_from_env`."""

    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return NullCrmDeliveryStore()
    return SqlAlchemyCrmDeliveryStore(create_engine(database_url, future=True))


def flush_crm_deliveries_from_env(*, limit: int = 100) -> dict[str, object]:
    """Retry every queued CRM touch. Counts only; never raises to the caller.

    Runs even when people search is unconnected: a CRM outage yesterday
    should not wait on a working search to be repaired. Without cycle-2
    isolation the queue is left untouched so a working CRM is not poked.
    """

    return flush_deliveries(http_sink_from_env(), crm_delivery_store_from_env(), limit=limit)
