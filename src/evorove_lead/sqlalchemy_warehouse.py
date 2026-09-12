"""SQLAlchemy implementation of the analysis-warehouse port.

The engine and re-analysis code depend on `warehouse.AnalysisWarehouse`,
not on this module. Keep the ORM here so the ports stay swappable.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from evorove_lead.sqlalchemy_models import (
    Base,
    BriefRow,
    CandidateRow,
    HypothesisOutcomeRow,
    HypothesisRow,
    RejectedTraceRow,
    TraceRow,
)
from evorove_lead.warehouse import (
    AnalysisWarehouse,
    BriefRecord,
    CandidateRecord,
    HypothesisOutcomeRecord,
    HypothesisRecord,
    NullAnalysisWarehouse,
    RejectedTraceRecord,
    TraceRecord,
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


class SqlAlchemyAnalysisWarehouse:
    """Tenant-scoped analysis warehouse backed by Postgres (or SQLite in tests)."""

    def __init__(self, engine) -> None:
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


def create_all(engine) -> None:
    """Test/dev convenience: create tables without Alembic. Prod uses migrations."""

    Base.metadata.create_all(engine)


def warehouse_from_env() -> AnalysisWarehouse:
    """No `DATABASE_URL` -> `NullAnalysisWarehouse`, same fallback shape as `crm_touch.sink_from_env`."""

    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return NullAnalysisWarehouse()
    return SqlAlchemyAnalysisWarehouse(create_engine(database_url, future=True))
