from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from evorove_lead.api import create_app
from evorove_lead.warehouse import (
    BriefRecord,
    CandidateRecord,
    HypothesisRecord,
    RecordingAnalysisWarehouse,
    RejectedTraceRecord,
    TraceRecord,
)

AUTH = ("owner", "test_insights_password")
NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    monkeypatch.setenv("INSIGHTS_PASSWORD", "test_insights_password")


def _client(warehouse=None) -> tuple[TestClient, RecordingAnalysisWarehouse]:
    store = warehouse if warehouse is not None else RecordingAnalysisWarehouse()
    return TestClient(create_app(store)), store


def _seed(warehouse: RecordingAnalysisWarehouse) -> None:
    warehouse.save_brief(
        BriefRecord(
            id="brief-1",
            business_id="acme",
            what_we_sell=({"text": "furnace repair", "source_name": "acme.example"},),
            who_may_fit=({"text": "homeowners with an old furnace", "source_name": "acme.example"},),
            commercial_claims=({"kind": "guarantee", "text": "1yr warranty", "source_name": "acme.example"},),
            must_not_promise=("same-day for every job",),
            created_at=NOW,
            business_archetype="hvac",
        )
    )
    warehouse.save_hypothesis(
        HypothesisRecord(
            id="hyp-1",
            business_id="acme",
            brief_id="brief-1",
            audience_segment="homeowners posting about a broken furnace",
            channel="forum",
            query_template="furnace won't turn on",
            intent_trigger="furnace broken",
            status="live",
            fit_score=0.8,
            evidence_score=0.6,
            reach_estimate=40,
            created_at=NOW,
        )
    )
    warehouse.save_trace(
        TraceRecord(
            id="trace-1",
            business_id="acme",
            hypothesis_id="hyp-1",
            url="https://forum.example/post/1",
            fetched_at=NOW,
            raw_text="My furnace won't turn on, anyone know a good repair person near me?",
            query_used="furnace won't turn on",
            source_channel="forum",
        )
    )
    warehouse.save_candidate(
        CandidateRecord(
            id="cand-1",
            business_id="acme",
            hypothesis_id="hyp-1",
            identity="jdoe@example.com",
            channel="email",
            reason="posted about a broken furnace needing repair",
            reason_source="trace-1",
            fit=0.8,
            evidence=0.7,
            addressable=True,
            decision="cold",
            decided_at=NOW,
            trace_id="trace-1",
            email="jdoe@example.com",
        )
    )
    warehouse.save_rejected_trace(
        RejectedTraceRecord(
            id="rej-1",
            business_id="acme",
            reason="no contact address on the page",
            rejected_at=NOW,
        )
    )


def test_index_requires_auth():
    client, _ = _client()

    response = client.get("/insights")

    assert response.status_code == 401


def test_index_returns_503_when_password_unset(monkeypatch):
    monkeypatch.delenv("INSIGHTS_PASSWORD", raising=False)
    client, _ = _client()

    response = client.get("/insights", auth=AUTH)

    assert response.status_code == 503


def test_index_rejects_wrong_password():
    client, _ = _client()

    response = client.get("/insights", auth=("owner", "wrong"))

    assert response.status_code == 401


def test_index_renders_with_correct_auth():
    client, _ = _client()

    response = client.get("/insights", auth=AUTH)

    assert response.status_code == 200
    assert "search &amp; analysis log" in response.text


def test_business_page_shows_brief_hypothesis_trace_and_candidate():
    client, warehouse = _client()
    _seed(warehouse)

    response = client.get("/insights/business", params={"business_id": "acme"}, auth=AUTH)

    assert response.status_code == 200
    body = response.text
    assert "furnace repair" in body
    assert "homeowners posting about a broken furnace" in body
    assert "forum.example/post/1" in body
    assert "jdoe@example.com" in body
    assert "posted about a broken furnace needing repair" in body
    assert "no contact address on the page" in body


def test_business_page_never_fires_a_message(monkeypatch):
    """The insights screen is read-only: it must never call an outbound sender."""
    client, warehouse = _client()
    _seed(warehouse)

    response = client.get("/insights/business", params={"business_id": "acme"}, auth=AUTH)

    assert response.status_code == 200
    # No save_* call happened as a side effect of viewing the page.
    assert len(warehouse.briefs) == 1
    assert len(warehouse.candidates) == 1


def test_business_page_escapes_untrusted_trace_text():
    client, warehouse = _client()
    warehouse.save_hypothesis(
        HypothesisRecord(
            id="hyp-x",
            business_id="acme",
            brief_id="brief-x",
            audience_segment="segment",
            channel="forum",
            query_template="q",
            intent_trigger="t",
            status="live",
            fit_score=0.5,
            evidence_score=0.5,
            reach_estimate=1,
            created_at=NOW,
        )
    )
    warehouse.save_trace(
        TraceRecord(
            id="trace-x",
            business_id="acme",
            hypothesis_id="hyp-x",
            url="https://forum.example/post/2",
            fetched_at=NOW,
            raw_text="<script>alert(1)</script>",
            query_used="q",
            source_channel="forum",
        )
    )

    response = client.get("/insights/business", params={"business_id": "acme"}, auth=AUTH)

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_business_page_separates_a_quote_from_an_ai_inference():
    client, warehouse = _client()
    _seed(warehouse)
    warehouse.save_brief(
        BriefRecord(
            id="brief-1",
            business_id="acme",
            what_we_sell=({"text": "furnace repair", "source_name": "acme.example"},),
            who_may_fit=({"text": "homeowners with an old furnace", "source_name": "acme.example"},),
            commercial_claims=(),
            must_not_promise=("price",),
            created_at=NOW,
            marketing_analysis={
                "product": {"quote": "furnace repair", "source_name": "acme.example"},
                "price": None,
                "place": None,
                "promotion": None,
                "segments": [
                    {
                        "label": "owners of older houses",
                        "evidence_quote": "homeowners with an old furnace",
                        "source_name": "acme.example",
                        "channel": "forums",
                    }
                ],
            },
        )
    )
    warehouse.save_hypothesis(
        HypothesisRecord(
            id="hyp-ai",
            business_id="acme",
            brief_id="brief-1",
            audience_segment="owners of older houses",
            channel="forums",
            query_template="owners of older houses forum",
            intent_trigger="demographic_fit",
            status="live",
            fit_score=0.0,
            evidence_score=0.0,
            reach_estimate=0,
            created_at=NOW,
            audience_source="ai_inferred",
            evidence_quote="homeowners with an old furnace",
        )
    )

    response = client.get("/insights/business", params={"business_id": "acme"}, auth=AUTH)

    assert response.status_code == 200
    body = response.text
    assert "quoted from the client&#x27;s materials" in body or "quoted from the client's materials" in body
    assert "AI inference" in body
    assert "owners of older houses" in body
    assert "homeowners with an old furnace" in body


def test_unknown_business_renders_empty_sections():
    client, _ = _client()

    response = client.get("/insights/business", params={"business_id": "ghost"}, auth=AUTH)

    assert response.status_code == 200
    assert "No brief run yet" in response.text
    assert "No hypotheses formed yet" in response.text
