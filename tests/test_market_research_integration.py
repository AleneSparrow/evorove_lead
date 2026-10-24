"""Real entry point and research components; only external I/O is scripted."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from evorove_lead import client_zero
from evorove_lead.business_dna import BusinessDNA
from evorove_lead.intelligence_store import IntelligenceStore
from evorove_lead.research_budget import BudgetLimits
from evorove_lead.sqlalchemy_models import Base, CandidateRow, CrmDeliveryRow
from evorove_lead.business import BusinessSeed
from intelligence_helpers import Completion, Search, Presence, fetch
from test_research_pipeline import setup_pipeline


class MarketAwareCompletion(Completion):
    """A controlled LLM whose buyer choice actually depends on market evidence."""
    def __init__(self):
        super().__init__()
        self.inputs = []

    def complete_bounded(self, *, system, user, max_tokens, effort=None, **kwargs):
        data = json.loads(user)
        self.inputs.append((system, data))
        reply = super().complete_bounded(system=system, user=user, max_tokens=max_tokens, effort=effort)
        if "Task: Name buyers" in system:
            market = data["market"]
            claim = market["evidence"]["claims"][0]
            output = json.loads(reply.text)
            h = output["hypotheses"][0]
            # The changed buyer query must retrieve a different result below.
            h["potential_problem"] = "staffed reception" if "staffed reception" in claim["text"] else "unserved demand"
            h["supporting_claims"] = [claim["id"]]
            reply = reply.__class__(json.dumps(output), reply.input_tokens, reply.output_tokens)
        if "Task: Assess six" in system or "Task: Independently challenge" in system:
            assert data["market"]["evidence"]["business_id"] == data["dna"]["business_id"]
            assert data["market"]["supporting_claims"]
        return reply


class MarketDrivenSearch(Search):
    def search(self, query):
        if query == "market demand evidence" or "staffed reception" in query:
            return super().search(query)
        self.queries.append(query)
        return ()


def test_client_zero_uses_market_evidence_and_cached_report(monkeypatch, tmp_path):
    # Exercise the real configuration factory, LeadGenerationEngine and dry-run CLI.
    db_url = f"sqlite:///{tmp_path / 'pilot.db'}"
    db = create_engine(db_url)
    Base.metadata.create_all(db)
    for name, value in {
        "DATABASE_URL": db_url, "WEB_SEARCH_BASE_URL": "http://127.0.0.1:8080",
        "EVOROVE_CLIENT_ZERO_BUSINESS_ID": "pilot-a", "RESEARCH_ENABLED": "1",
        "RESEARCH_ALLOW_PAID_LLM": "1", "RESEARCH_CRM_DELIVERY": "0",
        "ANTHROPIC_API_KEY": "synthetic-unused", "ANTHROPIC_MODEL": "claude-sonnet-4-5",
        "RESEARCH_INPUT_USD_PER_MTOK": "3", "RESEARCH_OUTPUT_USD_PER_MTOK": "15",
        "AI_PROVIDER": "deterministic",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    monkeypatch.setattr(client_zero, "OWNER_MATERIALS_DIR", tmp_path / "empty-materials")
    completion, search = MarketAwareCompletion(), MarketDrivenSearch()
    monkeypatch.setattr("evorove_lead.presence.HttpPresenceSource.load", lambda self, seed: Presence().load(seed))
    monkeypatch.setattr("evorove_lead.web_search.HttpSearxngWebSearchClient.search", lambda self, query: search.search(query))
    monkeypatch.setattr("evorove_lead.research_config.AnthropicResearchCompletion.complete_bounded", lambda self, **kw: completion.complete_bounded(**kw))
    monkeypatch.setattr("evorove_lead.research_runtime.fetch_source_snapshot", fetch)

    summary = client_zero.run_client_zero_search(research_dry_run=True)
    assert summary["research_status"] == "completed"
    assert summary["candidates"] == 1
    assert summary["handoffs"] == summary["crm_pending"] == summary["messages_sent"] == 0
    store = IntelligenceStore(db)
    run = store.get("pilot-a", "run", summary["research_run_id"])
    market = store.get("pilot-a", "market", run["market_document_id"])
    assert market["evidence"]["business_id"] == "pilot-a"
    assert store.get("other-tenant", "market", run["market_document_id"]) is None
    assert "staffed reception" in store.list("pilot-a", "icp")[0]["potential_problem"]
    assert any("staffed reception" in q for q in search.queries)
    tasks = completion.calls
    prefixes = ["Understand the business", "Plan research questions", "Interpret retrieved market", "Name buyers", "Extract claims", "Assess six", "Independently challenge"]
    assert [next(i for i, task in enumerate(tasks) if task.startswith(p)) for p in prefixes] == sorted(next(i for i, task in enumerate(tasks) if task.startswith(p)) for p in prefixes)
    planning = next(data for system, data in completion.inputs if "Task: Plan research questions" in system)
    assert planning["dna"]["business_id"] == "pilot-a"
    assert "evidence" not in planning["dna"]
    assert any("scheduling software" in text.casefold() for text in planning["dna"]["services"])
    qualification = store.list("pilot-a", "qualification")[0]
    assert qualification["market_document_id"] == run["market_document_id"]
    assert qualification["gate"]["decision"] == "QUALIFIED"
    assert "jane@buyer.example" in qualification["gate"]["reason"]
    assert "staffed reception" not in qualification["gate"]["reason"]

    # New entry-point run reuses the report rather than paying twice for research.
    again = client_zero.run_client_zero_search(research_dry_run=True)
    assert again["research_status"] == "completed"
    assert store.get("pilot-a", "run", again["research_run_id"])["market_cache_hit"]
    assert sum(t.startswith("Plan research questions") for t in completion.calls) == 1
    assert sum(t.startswith("Interpret retrieved market") for t in completion.calls) == 1
    with Session(db) as session:
        assert session.scalar(select(func.count()).select_from(CandidateRow)) == 0
        assert session.scalar(select(func.count()).select_from(CrmDeliveryRow)) == 0
    db.dispose()


def test_market_result_changes_qualified_outcome_without_becoming_person_evidence(tmp_path):
    outcomes = []
    for alternative in (True, False):
        root = tmp_path / str(alternative)
        root.mkdir()
        db, pipeline, engine, crm, delivery = setup_pipeline(root)
        pipeline.completion = MarketAwareCompletion()
        pipeline.client = MarketDrivenSearch()
        def load(url, business_id, subject):
            source = fetch(url, business_id, subject)
            if subject == "market" and not alternative:
                source = source.model_copy(update={"content": "Published demand for unserved demand in the US.", "content_hash": ""})
                source = type(source).model_validate(source.model_dump())
            return source
        pipeline.fetcher = load
        result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
        outcomes.append(len(result.handoffs))
        if alternative:
            again = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
            assert not again.handoffs and len(crm.accepted) == 1
        assert delivery.pending_count() == 0
        db.dispose()
    assert outcomes == [1, 0]


@pytest.mark.parametrize("failure", ["empty", "invalid", "budget"])
def test_market_failure_stops_before_prospect_admission(tmp_path, failure):
    db, pipeline, engine, crm, delivery = setup_pipeline(tmp_path)
    if failure == "empty":
        class EmptyMarket(Search):
            def search(self, query):
                return () if query == "market demand evidence" else super().search(query)
        pipeline.client = EmptyMarket()
    elif failure == "invalid":
        class InvalidMarket(Completion):
            def complete_bounded(self, **kw):
                reply = super().complete_bounded(**kw)
                if "Task: Interpret retrieved market" in kw["system"]:
                    data = json.loads(reply.text)
                    data["evidence"]["business_id"] = "wrong-tenant"
                    return type(reply)(json.dumps(data), 1000, 500)
                return reply
        pipeline.completion = InvalidMarket()
    else:
        pipeline.limits = BudgetLimits(queries=0)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert not result.handoffs and not crm.accepted and delivery.pending_count() == 0
    assert pipeline.last_run["status"] == ("budget_exhausted" if failure == "budget" else "failed")
    from evorove_lead.engine import GenerationStatus
    if failure != "budget":
        assert result.status is GenerationStatus.FAILED
        assert result.status is not GenerationStatus.NO_FIT
    assert "hypothesis_generation" not in pipeline.last_run["stages"]
    assert "market_document_id" not in pipeline.last_run
    assert pipeline.last_run["usage"]["queries"] <= pipeline.limits.queries
    db.dispose()


def test_market_cache_is_researched_after_expiry(tmp_path):
    db, pipeline, engine, crm, delivery = setup_pipeline(tmp_path, dry_run=True)
    seed = BusinessSeed(site_url="https://seller.example", business_id="a")
    engine.generate(seed)
    # Expire through the actual cache API, without changing the pipeline's clock.
    dna = BusinessDNA.model_validate(pipeline.store.get("a", "dna", pipeline.last_run["dna_version"]))
    market_key = pipeline.cache.key("market-1-integrated", pipeline.model, dna.model_dump_json())
    report = pipeline.store.get("a", "market", pipeline.last_run["market_document_id"])
    pipeline.cache.put("a", market_key, report, timedelta(seconds=-1))
    engine.generate(seed)
    assert pipeline.last_run["status"] == "completed"
    assert not pipeline.last_run["market_cache_hit"]
    assert sum(t.startswith("Plan research questions") for t in pipeline.completion.calls) == 2
    engine.generate(BusinessSeed(site_url="https://seller.example", business_id="b"))
    assert not pipeline.last_run["market_cache_hit"]
    market = pipeline.store.get("b", "market", pipeline.last_run["market_document_id"])
    assert market["evidence"]["business_id"] == "b"
    assert all(source["business_id"] == "b" for source in market["evidence"]["sources"])
    assert sum(t.startswith("Plan research questions") for t in pipeline.completion.calls) == 3
    db.dispose()


def test_no_reflection_revision_beyond_iteration_or_query_budget(tmp_path):
    db, pipeline, engine, crm, delivery = setup_pipeline(tmp_path)
    class OnlyMarket(Search):
        def search(self, query):
            if query == "market demand evidence":
                return super().search(query)
            self.queries.append(query)
            return ()
    pipeline.client = OnlyMarket()
    pipeline.limits = BudgetLimits(queries=2, iterations=1)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert not result.handoffs and not crm.accepted
    assert pipeline.last_run["status"] == "completed"
    assert pipeline.last_run["usage"]["queries"] == 2  # market + one prospect query
    assert pipeline.last_run["usage"]["iterations"] == 1
    assert not any(t.startswith("Analyze failed research") for t in pipeline.completion.calls)
    db.dispose()
