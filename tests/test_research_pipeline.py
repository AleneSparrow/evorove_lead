import pytest
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import Session
from evorove_lead.sqlalchemy_models import Base, CandidateRow, CrmDeliveryRow
from evorove_lead.sqlalchemy_warehouse import SqlAlchemyAnalysisWarehouse, SqlAlchemyCrmDeliveryStore
from evorove_lead.research_runtime import ResearchPipeline
from evorove_lead.research_delivery import AdmissionRow
from evorove_lead.crm_touch import OutboxCrmLeadTouchSink, CrmDeliveryError, redeliver_pending
from evorove_lead.engine import LeadGenerationEngine
from evorove_lead.business import BusinessSeed
from intelligence_helpers import *

class CRM:
    def __init__(self, fail=False): self.fail, self.accepted = fail, {}
    def publish(self, business_id, payload):
        if self.fail: raise CrmDeliveryError("unavailable")
        self.accepted[(business_id, payload["touch_id"])] = payload

def setup_pipeline(tmp_path, *, dry_run=False, contradiction=False, fail_crm=False):
    db = create_engine(f"sqlite:///{tmp_path / 'research.db'}")
    Base.metadata.create_all(db)
    warehouse = SqlAlchemyAnalysisWarehouse(db)
    completion, search, crm = Completion(contradiction), Search(), CRM(fail_crm)
    pipeline = ResearchPipeline(db, search, completion, fetcher=fetch, clock=lambda: NOW, dry_run=dry_run)
    store = SqlAlchemyCrmDeliveryStore(db)
    sink = OutboxCrmLeadTouchSink(crm, store)
    engine = LeadGenerationEngine(presence=Presence(), warehouse=warehouse, lead_touch_sink=sink, research_pipeline=pipeline)
    return db, pipeline, engine, crm, store

def test_full_sequence_to_atomic_cold_and_duplicate_run(tmp_path):
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path)
    seed = BusinessSeed(site_url="https://seller.example", business_id="a")
    result = engine.generate(seed)
    assert len(result.handoffs) == 1, pipeline.last_run
    assert result.sent_messages == ()
    stages = pipeline.last_run["stages"]
    required = ["business_analysis", "market_research", "hypothesis_generation", "prospect_search", "evidence_collection", "qualification", "adversarial_review", "qualification_gate", "crm_cold"]
    assert [stages.index(s) for s in required] == sorted(stages.index(s) for s in required)
    payload = next(iter(crm.accepted.values()))
    assert payload["payload"]["hypothesis_id"] == result.handoffs[0].hypothesis_id
    assert payload["kind"] == "assembled" and payload["cycle"] == 1
    assert store.pending_count() == 0
    again = engine.generate(seed)
    assert again.handoffs == ()
    assert len(crm.accepted) == 1
    with Session(db) as session:
        assert session.scalar(select(func.count()).select_from(CandidateRow)) == 1
        assert session.scalar(select(func.count()).select_from(AdmissionRow)) == 1

def test_dry_run_does_not_queue_delivery(tmp_path):
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert len(result.candidates) == 1, pipeline.last_run
    assert result.handoffs == () and not crm.accepted and store.pending_count() == 0

def test_conflicting_evidence_never_enters_cold(tmp_path):
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, contradiction=True)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.handoffs == () and not crm.accepted
    assert result.rejected[0].why == "critical_contradiction"

def test_crm_outage_and_recovery_use_existing_outbox(tmp_path):
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, fail_crm=True)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert len(result.handoffs) == 1 and store.pending_count() == 1
    crm.fail = False
    assert redeliver_pending(crm, store) == (1, 0)
    assert len(crm.accepted) == 1

def test_database_failure_rolls_back_candidate_and_admission(tmp_path):
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path)
    def fail(*args): raise RuntimeError("simulated DB interruption")
    event.listen(CrmDeliveryRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    finally: event.remove(CrmDeliveryRow, "before_insert", fail)
    with Session(db) as session:
        assert session.scalar(select(func.count()).select_from(CandidateRow)) == 0
        assert session.scalar(select(func.count()).select_from(AdmissionRow)) == 0
    assert not crm.accepted

def test_reflection_revises_empty_search_then_qualifies(tmp_path):
    import json
    from evorove_lead.llm_usage import CompletionReply
    class RetrySearch(Search):
        def search(self, query):
            if query == '"intake coordination" "small businesses" US':
                self.queries.append(query)
                return ()
            return super().search(query)
    class ReflectingCompletion(Completion):
        def complete_bounded(self, *, system, user, max_tokens):
            if "Task: Analyze failed research" in system:
                data = json.loads(user)
                return CompletionReply(json.dumps({"cause": "query too narrow", "evidence": [data["history"][-1]["observation_id"]], "justified": True, "revised_queries": ["published intake request US"], "explanation": "search an explicit problem statement"}), 1000, 100)
            return super().complete_bounded(system=system, user=user, max_tokens=max_tokens)
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path)
    pipeline.client = RetrySearch()
    pipeline.completion = ReflectingCompletion()
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert len(result.handoffs) == 1, pipeline.last_run
    assert pipeline.last_run["usage"]["iterations"] == 2
    reflections = pipeline.store.list("a", "reflection")
    assert any("strategy_change" in r for r in reflections)


def test_unread_client_site_is_brief_unavailable_not_no_fit(tmp_path):
    from evorove_lead.engine import GenerationStatus
    from evorove_lead.presence import PresenceRejected

    class DeadSite:
        def load(self, seed):
            raise PresenceRejected("owner site could not be read")

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    engine._presence = DeadSite()
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status is GenerationStatus.FAILED
    assert result.status is not GenerationStatus.NO_FIT
    assert result.stop_reason == "brief_unavailable"
    assert result.offer is None
    assert result.candidates == ()
    assert result.handoffs == ()
    assert pipeline.last_run["status"] == "failed"
    assert pipeline.last_run["stop_reason"] == "brief_unavailable"
    assert pipeline.last_run["failed_stage"] == "business_analysis"
    assert pipeline.last_run["usage"]["queries"] == 0
    assert pipeline.client.queries == []
    assert not crm.accepted and store.pending_count() == 0


def test_completed_search_with_no_qualified_people_stays_no_fit(tmp_path):
    from evorove_lead.engine import GenerationStatus

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, contradiction=True)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status is GenerationStatus.NO_FIT
    assert result.stop_reason == ""
    assert result.handoffs == ()
    assert result.rejected
    assert pipeline.last_run["status"] == "completed"
    assert pipeline.last_run["usage"]["queries"] >= 1
    assert pipeline.client.queries
    assert not crm.accepted and store.pending_count() == 0


def test_search_failure_is_recorded_and_does_not_publish(tmp_path):
    class BrokenSearch:
        def search(self, query): raise RuntimeError("search unavailable")
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path)
    pipeline.client = BrokenSearch()
    with pytest.raises(RuntimeError): engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert pipeline.last_run["status"] == "failed"
    assert not crm.accepted and store.pending_count() == 0


def test_budget_exhaustion_never_falls_back_to_legacy_selection(tmp_path):
    from evorove_lead.research_budget import BudgetLimits
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path)
    pipeline.limits = BudgetLimits(llm_calls=0)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert not result.handoffs and not crm.accepted
    assert pipeline.last_run["status"] == "budget_exhausted"

def test_insights_report_real_run_status_without_inventing_metrics(tmp_path):
    from evorove_lead.insights import _research_section
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    html = _research_section(engine._warehouse, "a")
    assert "Dry run" in html and "1 qualified" in html
    assert "QUALIFIED" in html
    assert _research_section(engine._warehouse, "other") == ""
