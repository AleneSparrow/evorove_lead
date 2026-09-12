"""Write/read tests for the analysis warehouse, tenant-scoped by business_id."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from evorove_lead.sqlalchemy_warehouse import SqlAlchemyAnalysisWarehouse, create_all
from evorove_lead.warehouse import (
    BriefRecord,
    CandidateRecord,
    HypothesisOutcomeRecord,
    HypothesisRecord,
    NullAnalysisWarehouse,
    RejectedTraceRecord,
    TraceRecord,
)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


@pytest.fixture()
def warehouse(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'warehouse.db'}", future=True)
    create_all(engine)
    return SqlAlchemyAnalysisWarehouse(engine)


def _brief(business_id: str, brief_id: str) -> BriefRecord:
    return BriefRecord(
        id=brief_id,
        business_id=business_id,
        what_we_sell=({"text": "haircuts", "source_name": "site"},),
        who_may_fit=({"text": "for busy professionals", "source_name": "site"},),
        commercial_claims=(),
        must_not_promise=("price", "discount", "guarantee", "legal_claim"),
        created_at=NOW,
    )


def _hypothesis(business_id: str, brief_id: str, hypothesis_id: str) -> HypothesisRecord:
    return HypothesisRecord(
        id=hypothesis_id,
        business_id=business_id,
        brief_id=brief_id,
        audience_segment="busy professionals",
        channel="web_search",
        query_template="haircut near {geo}",
        intent_trigger="need_statement",
        status="live",
        fit_score=0.8,
        evidence_score=0.6,
        reach_estimate=12,
        created_at=NOW,
    )


def test_brief_round_trips_and_is_tenant_scoped(warehouse):
    warehouse.save_brief(_brief("biz-a", "brief-1"))
    warehouse.save_brief(_brief("biz-b", "brief-2"))

    briefs = warehouse.list_briefs("biz-a")

    assert [b.id for b in briefs] == ["brief-1"]
    assert briefs[0].what_we_sell[0]["text"] == "haircuts"


def test_hypothesis_round_trips_and_is_tenant_scoped(warehouse):
    warehouse.save_brief(_brief("biz-a", "brief-1"))
    warehouse.save_hypothesis(_hypothesis("biz-a", "brief-1", "hyp-1"))
    warehouse.save_brief(_brief("biz-b", "brief-2"))
    warehouse.save_hypothesis(_hypothesis("biz-b", "brief-2", "hyp-2"))

    hypotheses = warehouse.list_hypotheses("biz-a")

    assert [h.id for h in hypotheses] == ["hyp-1"]
    assert hypotheses[0].intent_trigger == "need_statement"
    assert hypotheses[0].status == "live"
    # Phase 4 fields default to unrated until reweight_hypotheses.py runs.
    assert hypotheses[0].accept_rate == 0.0
    assert hypotheses[0].close_rate == 0.0
    assert hypotheses[0].query_budget == 1


def test_hypothesis_budget_fields_round_trip(warehouse):
    from dataclasses import replace

    warehouse.save_brief(_brief("biz-a", "brief-1"))
    warehouse.save_hypothesis(
        replace(
            _hypothesis("biz-a", "brief-1", "hyp-1"),
            accept_rate=0.5,
            close_rate=0.4,
            query_budget=3,
        )
    )

    hypothesis = warehouse.list_hypotheses("biz-a")[0]

    assert hypothesis.accept_rate == 0.5
    assert hypothesis.close_rate == 0.4
    assert hypothesis.query_budget == 3


def test_trace_round_trips_and_is_tenant_scoped(warehouse):
    warehouse.save_brief(_brief("biz-a", "brief-1"))
    warehouse.save_hypothesis(_hypothesis("biz-a", "brief-1", "hyp-1"))
    warehouse.save_trace(
        TraceRecord(
            id="trace-1",
            business_id="biz-a",
            hypothesis_id="hyp-1",
            url="https://example.com/post/1",
            fetched_at=NOW,
            raw_text="looking for a barber near downtown",
            query_used="haircut near downtown",
            source_channel="web_search",
            language="en",
            geo_hint="downtown",
        )
    )

    traces = warehouse.list_traces("biz-a", "hyp-1")

    assert [t.id for t in traces] == ["trace-1"]
    assert traces[0].url == "https://example.com/post/1"
    assert warehouse.list_traces("biz-b", "hyp-1") == ()


def test_rejected_trace_round_trips_and_is_tenant_scoped(warehouse):
    warehouse.save_rejected_trace(
        RejectedTraceRecord(
            id="rej-1",
            business_id="biz-a",
            trace_id="trace-1",
            reason="geo does not match service area",
            rejected_at=NOW,
        )
    )
    warehouse.save_rejected_trace(
        RejectedTraceRecord(id="rej-2", business_id="biz-b", reason="stale", rejected_at=NOW)
    )

    rejected = warehouse.list_rejected_traces("biz-a")

    assert [r.id for r in rejected] == ["rej-1"]
    assert rejected[0].reason == "geo does not match service area"


def test_candidate_round_trips_and_is_tenant_scoped(warehouse):
    warehouse.save_brief(_brief("biz-a", "brief-1"))
    warehouse.save_hypothesis(_hypothesis("biz-a", "brief-1", "hyp-1"))
    warehouse.save_candidate(
        CandidateRecord(
            id="cand-1",
            business_id="biz-a",
            hypothesis_id="hyp-1",
            identity="Jane, jane@example.com",
            channel="email",
            reason="publicly asked for a barber recommendation",
            reason_source="https://example.com/post/1",
            fit=0.9,
            evidence=0.7,
            addressable=True,
            decision="cold",
            decided_at=NOW,
            email="jane@example.com",
        )
    )

    candidates = warehouse.list_candidates("biz-a", "hyp-1")

    assert [c.id for c in candidates] == ["cand-1"]
    assert candidates[0].decision == "cold"
    assert candidates[0].email == "jane@example.com"


def test_hypothesis_outcome_round_trips_and_is_tenant_scoped(warehouse):
    warehouse.save_brief(_brief("biz-a", "brief-1"))
    warehouse.save_hypothesis(_hypothesis("biz-a", "brief-1", "hyp-1"))
    warehouse.save_hypothesis_outcome(
        HypothesisOutcomeRecord(
            id="out-1",
            business_id="biz-a",
            hypothesis_id="hyp-1",
            case_id="case-1",
            outcome="done",
            recorded_at=NOW,
        )
    )

    outcomes = warehouse.list_hypothesis_outcomes("biz-a", "hyp-1")

    assert [o.id for o in outcomes] == ["out-1"]
    assert outcomes[0].outcome == "done"
    assert warehouse.list_hypothesis_outcomes("biz-b", "hyp-1") == ()


def test_null_warehouse_writes_nothing_and_reads_empty():
    warehouse = NullAnalysisWarehouse()

    warehouse.save_brief(_brief("biz-a", "brief-1"))
    warehouse.save_hypothesis(_hypothesis("biz-a", "brief-1", "hyp-1"))

    assert warehouse.list_briefs("biz-a") == ()
    assert warehouse.list_hypotheses("biz-a") == ()
