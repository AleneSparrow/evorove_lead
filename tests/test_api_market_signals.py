"""Track 4's HTTP surface: POST /api/v1/internal/market-signals/run-due."""

import json

import pytest
from fastapi.testclient import TestClient

from evorove_lead.api import create_app
from evorove_lead.engine import GenerationResult, GenerationStatus
from evorove_lead.market_signal_llm import ScriptedMarketSignalCompletion
from evorove_lead.market_signals import InMemoryMarketSignalStore
from evorove_lead.searches import InMemorySearchTargetStore, SearchTarget
from evorove_lead.warehouse import RecordingAnalysisWarehouse
from evorove_lead.web_search import SearchHit
from datetime import datetime, timezone

HEADERS = {"X-Internal-Task-Secret": "local_development_only"}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")


class _FakeEngine:
    def generate(self, seed, *, extra_hypotheses=()):
        del seed
        return GenerationResult(
            status=GenerationStatus.PEOPLE_FOUND if extra_hypotheses else GenerationStatus.NO_FIT,
            offer=None,
            candidates=(),
            handoffs=tuple(object() for _ in extra_hypotheses),
            rejected=(),
        )


class _FakeWebSearchClient:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query: str):
        del query
        return self.hits


def _relevant_payload() -> str:
    return json.dumps(
        {
            "relevant": True,
            "segment_label": "salons hiring booth renters as employees",
            "channel": "industry_sites",
            "evidence_quote": "New licensing rules",
        }
    )


def _app(**kwargs):
    targets = kwargs.pop("targets", InMemorySearchTargetStore())
    return create_app(
        RecordingAnalysisWarehouse(),
        targets=targets,
        engine_factory=lambda: _FakeEngine(),
        **kwargs,
    ), targets


def test_run_due_requires_the_task_secret():
    app, _ = _app()
    client = TestClient(app)

    assert client.post("/api/v1/internal/market-signals/run-due").status_code == 401


def test_run_due_market_signals_end_to_end():
    targets = InMemorySearchTargetStore()
    targets.save(SearchTarget("tenant-a", "https://acme.com/", "salon software", datetime.now(timezone.utc)))
    app, _ = _app(
        targets=targets,
        signal_store=InMemoryMarketSignalStore(),
        signal_web_search_client_factory=lambda: _FakeWebSearchClient(
            (SearchHit(url="https://news.example/1", snippet="New licensing rules"),)
        ),
        signal_llm_factory=lambda: ScriptedMarketSignalCompletion(_relevant_payload()),
    )
    client = TestClient(app)

    response = client.post("/api/v1/internal/market-signals/run-due", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"checked": 1, "triggered": 1, "cold": 1}


def test_run_due_market_signals_503s_when_web_search_is_unconnected():
    app, _ = _app(signal_web_search_client_factory=lambda: None)
    client = TestClient(app)

    response = client.post("/api/v1/internal/market-signals/run-due", headers=HEADERS)

    assert response.status_code == 503
    assert "WEB_SEARCH_BASE_URL" in response.json()["detail"]


def test_run_due_market_signals_503s_when_llm_is_not_enabled():
    app, _ = _app(
        signal_web_search_client_factory=lambda: _FakeWebSearchClient(()),
        signal_llm_factory=lambda: None,
    )
    client = TestClient(app)

    response = client.post("/api/v1/internal/market-signals/run-due", headers=HEADERS)

    assert response.status_code == 503
    assert "AI_PROVIDER" in response.json()["detail"]
