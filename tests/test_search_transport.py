"""Real transport adapter / budget / pipeline. No external service calls."""
import io
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from evorove_lead.web_search import HttpSearxngWebSearchClient, SearchError, client_from_env
from evorove_lead.research_budget import BudgetedSearch, BudgetLimits, ResearchBudget
from evorove_lead.business import BusinessSeed
from evorove_lead.api import create_app
from evorove_lead.searches import InMemorySearchTargetStore, SearchTarget, run_target, live_engine, run_due, run_due_market_signals
from evorove_lead import client_zero, run
from intelligence_helpers import Completion, Presence, fetch
from test_research_pipeline import setup_pipeline


def response(results=(), **extra):
    return io.BytesIO(json.dumps({"results": list(results), **extra}).encode())


HIT = {"url": "https://buyer.example/news", "content": "published request"}


@pytest.mark.parametrize("body,code", [
    (b"not json", "search_invalid_json"), (b"\xff", "search_invalid_json"),
    (b"[]", "search_invalid_structure"), (b"{}", "search_invalid_structure"),
    (b'{"results":null}', "search_invalid_structure"),
    (b'{"results":[null]}', "search_invalid_structure"),
    (b'{"results":[{"url":5}]}', "search_invalid_structure"),
    (b'{"results":[{"url":"https://example.com","content":false}]}', "search_invalid_structure"),
])
def test_invalid_response_is_not_cached(body, code):
    calls = []
    def opener(*args, **kw):
        calls.append(1)
        return io.BytesIO(body) if len(calls) == 1 else response()
    client = HttpSearxngWebSearchClient("http://localhost:8080", opener)
    with pytest.raises(SearchError, match=code):
        client.search("need")
    assert client.search("need") == ()
    assert client.search("need") == ()
    assert len(calls) == 2  # genuine empty cached; error was not


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 501, 502, 503, 504, 505])
def test_http_status_retry_rules_and_attempt_budget(status):
    calls, waits = [], []
    def opener(request, **kw):
        calls.append(1)
        raise HTTPError(request.full_url, status, "private body", {"Retry-After": "0"}, None)
    client = HttpSearxngWebSearchClient("http://localhost:8080", opener)
    budget = ResearchBudget("a")
    search = BudgetedSearch(client, budget, sleeper=waits.append)
    with pytest.raises(SearchError, match=f"search_http_{status}"):
        search.search("need")
    attempts = 2 if status in {429, 500, 502, 503, 504} else 1
    assert len(calls) == budget.used["queries"] == attempts
    assert len(waits) == attempts - 1
    assert [e.code for e in search.errors] == [f"search_http_{status}"]


@pytest.mark.parametrize("error,code", [(TimeoutError(), "search_timeout"), (URLError("private endpoint"), "search_network_error")])
def test_network_failure_retry_and_recovery(error, code):
    calls = []
    def opener(*args, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise error
        return response([HIT])
    budget = ResearchBudget("a")
    search = BudgetedSearch(HttpSearxngWebSearchClient("http://localhost", opener), budget, sleeper=lambda _: None)
    assert search.search("need")[0].url == HIT["url"]
    assert budget.used["queries"] == 2 and not search.errors


def test_retry_after_too_long_and_internal_limit():
    waits = []
    def limited(request, **kw):
        raise HTTPError(request.full_url, 429, "limited", {"Retry-After": "120"}, None)
    budget = ResearchBudget("a")
    search = BudgetedSearch(HttpSearxngWebSearchClient("http://localhost", limited), budget, sleeper=waits.append)
    with pytest.raises(SearchError, match="search_http_429"):
        search.search("need")
    assert budget.used["queries"] == 1 and waits == []
    client = HttpSearxngWebSearchClient("http://localhost", lambda *a, **kw: response(), max_requests=1)
    assert client.search("one") == ()
    assert client.search("one") == ()  # cache doesn't consume a transport attempt
    with pytest.raises(SearchError, match="search_request_limit"):
        client.search("two")
    assert client.requests == 1


def test_live_configuration_is_required_but_stub_remains_optional(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    monkeypatch.delenv("RESEARCH_ENABLED", raising=False)
    assert client_from_env() is None
    with pytest.raises(SearchError, match="search_not_configured"):
        live_engine()
    monkeypatch.setenv("RESEARCH_ENABLED", "1")
    assert client_zero.run_client_zero_search(research_dry_run=True)["stop_reason"] == "search_not_configured"
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "ftp://invalid")
    with pytest.raises(SearchError, match="search_invalid_configuration"):
        client_from_env()


@pytest.mark.parametrize("kind,expected", [("partial", "partial"), ("failed", "failed"), ("empty", "no_fit"), ("limit", "budget_exhausted")])
def test_actual_adapter_through_pipeline_and_api_preserves_outcome(tmp_path, monkeypatch, kind, expected):
    db, pipeline, engine, crm, delivery = setup_pipeline(tmp_path)
    requests = []
    def opener(request, **kw):
        query = parse_qs(urlparse(request.full_url).query)["q"][0]
        requests.append(query)
        if kind == "failed":
            raise TimeoutError()
        if query == "market demand evidence":
            return response([{"url": "https://market.example/news", "content": "market evidence"}])
        return response([] if kind == "empty" else [HIT], **({"unresponsive_engines": [["engine", "timeout"]]} if kind == "partial" else {}))
    pipeline.client = HttpSearxngWebSearchClient("http://localhost", opener, max_requests=1 if kind == "limit" else 30)
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "synthetic")
    targets = InMemorySearchTargetStore()
    app = create_app(warehouse=engine._warehouse, targets=targets, engine_factory=lambda: engine)
    client = TestClient(app)
    headers = {"X-Internal-Task-Secret": "synthetic"}
    assert client.post("/api/v1/internal/searches", headers=headers, json={"business_id": "a", "site_url": "https://seller.example"}).status_code == 202
    outcome = client.get("/api/v1/internal/searches/a", headers=headers).json()
    assert outcome["status"] == expected
    assert targets.get("a").last_status != "running"
    assert outcome["cold"] == (1 if kind == "partial" else 0)
    if kind != "empty":
        reason = {"partial": "search_sources_unresponsive", "failed": "search_timeout", "limit": "search_request_limit"}[kind]
        assert outcome["stop_reason"] == pipeline.last_run["stop_reason"] == reason
        assert pipeline.last_run["status"] != "completed"
        assert not pipeline.store.list("a", "strategy_observation")
        assert not any(t.startswith("Analyze failed research") for t in pipeline.completion.calls)
        restarted = TestClient(create_app(warehouse=engine._warehouse, targets=targets, engine_factory=lambda: engine))
        assert restarted.get("/api/v1/internal/searches/a", headers=headers).json()["stop_reason"] == reason
    else:
        assert pipeline.last_run["status"] == "completed"
        assert "stop_reason" not in outcome
        assert pipeline.store.list("a", "strategy_observation")[0]["qualified"] == 0
    assert delivery.pending_count() == 0
    assert len(crm.accepted) == (1 if kind == "partial" else 0)
    if kind == "failed":
        assert len(requests) == 2
    db.dispose()


@pytest.mark.parametrize("fail_all", [False, True])
def test_multiple_market_queries_preserve_success_but_never_claim_complete(tmp_path, fail_all):
    db, pipeline, engine, crm, delivery = setup_pipeline(tmp_path)
    class Questions(Completion):
        def complete_bounded(self, **kw):
            reply = super().complete_bounded(**kw)
            if "Task: Plan research questions" in kw["system"]:
                return type(reply)(json.dumps({"queries": ["market demand evidence", "broken query"]}), 1000, 500)
            return reply
    pipeline.completion = Questions()
    requests = []
    def opener(request, **kw):
        query = parse_qs(urlparse(request.full_url).query)["q"][0]
        requests.append(query)
        if fail_all or query == "broken query":
            raise URLError("internal text")
        return response([{"url": "https://market.example/news" if query == "market demand evidence" else HIT["url"], "content": "evidence"}])
    pipeline.client = HttpSearxngWebSearchClient("http://localhost", opener)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status.value == ("failed" if fail_all else "partial")
    assert result.stop_reason == "search_network_error"
    assert len(result.handoffs) == (0 if fail_all else 1)
    assert not pipeline.store.list("a", "strategy_observation")
    if not fail_all:
        assert pipeline.last_run["market_document_id"]
    assert len(requests) <= 5
    db.dispose()


def test_cli_uses_real_factory_and_preserves_transport_reason(tmp_path, monkeypatch, capsys):
    from sqlalchemy import create_engine
    from evorove_lead.sqlalchemy_models import Base
    db_url = f"sqlite:///{tmp_path / 'cli.db'}"
    db = create_engine(db_url)
    Base.metadata.create_all(db)
    for key, value in {"DATABASE_URL": db_url, "WEB_SEARCH_BASE_URL": "http://localhost", "RESEARCH_ENABLED": "1", "RESEARCH_ALLOW_PAID_LLM": "1", "RESEARCH_INPUT_USD_PER_MTOK": "3", "RESEARCH_OUTPUT_USD_PER_MTOK": "15", "ANTHROPIC_API_KEY": "synthetic-unused", "ANTHROPIC_MODEL": "claude-sonnet-4-5", "AI_PROVIDER": "deterministic"}.items():
        monkeypatch.setenv(key, value)
    completion = Completion()
    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    monkeypatch.setattr("evorove_lead.presence.HttpPresenceSource.load", lambda self, seed: Presence().load(seed))
    monkeypatch.setattr("evorove_lead.research_runtime.fetch_source_snapshot", fetch)
    monkeypatch.setattr("evorove_lead.research_config.AnthropicResearchCompletion.complete_bounded", lambda self, **kw: completion.complete_bounded(**kw))
    def timeout(*a, **kw):
        raise TimeoutError()
    monkeypatch.setattr("evorove_lead.web_search.urlopen", timeout)
    assert run.main(["--business-id", "a", "--site-url", "https://seller.example"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed" and output["stop_reason"] == "search_timeout"
    assert output["cold"] == 0
    db.dispose()


def test_retry_after_wait_and_run_budget_bound_attempts():
    waits, calls = [], []
    def opener(request, **kw):
        calls.append(1)
        raise HTTPError(request.full_url, 429, "limited", {"Retry-After": "1"}, None)
    budget = ResearchBudget("a", BudgetLimits(queries=2))
    search = BudgetedSearch(HttpSearxngWebSearchClient("http://localhost", opener), budget, sleeper=waits.append)
    with pytest.raises(SearchError, match="search_http_429"):
        search.search("need")
    assert waits == [1] and len(calls) == budget.used["queries"] == 2
    waits.clear()
    search = BudgetedSearch(HttpSearxngWebSearchClient("http://localhost", opener), ResearchBudget("a", BudgetLimits(queries=1)), sleeper=waits.append)
    with pytest.raises(SearchError):
        search.search("need")
    assert waits == [] and search.budget.used["queries"] == 1


def test_oversized_response_and_source_failure_are_not_success_cache():
    from evorove_lead.presence import MAX_BODY_BYTES
    client = HttpSearxngWebSearchClient("http://localhost", lambda *a, **kw: io.BytesIO(b"x" * (MAX_BODY_BYTES + 1)))
    with pytest.raises(SearchError, match="search_response_too_large"):
        client.search("need")
    assert not client._cache
    client = HttpSearxngWebSearchClient("http://localhost", lambda *a, **kw: response([], unresponsive_engines=[["engine", "timeout"]]))
    with pytest.raises(SearchError, match="search_sources_unresponsive"):
        client.search("need")
    assert not client._cache


def test_missing_live_config_is_api_503_and_cli_failure(monkeypatch, capsys):
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    monkeypatch.setenv("RESEARCH_ENABLED", "1")
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "synthetic")
    client = TestClient(create_app())
    response_ = client.post("/api/v1/internal/searches", headers={"X-Internal-Task-Secret": "synthetic"}, json={"business_id": "a", "site_url": "https://seller.example"})
    assert response_.status_code == 503 and response_.json()["detail"] == "search_not_configured"
    assert run.main(["--business-id", "a", "--site-url", "https://seller.example"]) == 2
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "search_not_configured"


def test_scheduler_exposes_transport_failure_and_finishes_target(tmp_path):
    db, pipeline, engine, crm, delivery = setup_pipeline(tmp_path)
    def unavailable(*a, **kw):
        raise TimeoutError()
    pipeline.client = HttpSearxngWebSearchClient("http://localhost", unavailable)
    targets = InMemorySearchTargetStore()
    targets.save(SearchTarget("a", "https://seller.example", "", datetime.now(timezone.utc)))
    summary = run_due(targets, engine)
    assert summary["ran"] == 1 and summary["cold"] == 0
    assert summary["errors"] == [{"business_id": "a", "status": "failed", "stop_reason": "search_timeout"}]
    assert targets.get("a").last_status == "failed"
    signals = run_due_market_signals(targets, None, None, None, engine)
    assert signals["errors"][0]["stop_reason"] == "search_timeout"
    db.dispose()


def test_legacy_signal_transport_failure_is_not_no_news():
    from evorove_lead.engine import LeadGenerationEngine
    from evorove_lead.market_signals import InMemoryMarketSignalStore
    targets = InMemorySearchTargetStore()
    targets.save(SearchTarget("a", "https://seller.example", "software", datetime.now(timezone.utc)))
    def unavailable(*a, **kw):
        raise TimeoutError()
    client = HttpSearxngWebSearchClient("http://localhost", unavailable)
    result = run_due_market_signals(targets, InMemoryMarketSignalStore(), client, None, LeadGenerationEngine(presence=Presence()))
    assert result["triggered"] == 0
    assert result["errors"] == [{"business_id": "a", "status": "failed", "stop_reason": "search_timeout"}]
    assert targets.get("a").last_status == "failed"
