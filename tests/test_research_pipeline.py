import json
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
            if query == "small businesses intake coordination US":
                self.queries.append(query)
                return ()
            return super().search(query)
    class ReflectingCompletion(Completion):
        def complete_bounded(self, *, system, user, max_tokens, effort=None, **kwargs):
            if "Task: Analyze failed research" in system:
                data = json.loads(user)
                return CompletionReply(json.dumps({"cause": "query too narrow", "evidence": [data["history"][-1]["observation_id"]], "justified": True, "revised_queries": ["published intake request US"], "explanation": "search an explicit problem statement"}), 1000, 100)
            return super().complete_bounded(system=system, user=user, max_tokens=max_tokens, effort=effort)
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


def test_provider_failure_before_search_is_failed_not_no_fit(tmp_path):
    from evorove_lead.engine import GenerationStatus
    from evorove_lead.market_intelligence import ResearchUnavailable

    class Boom:
        def complete_bounded(self, **kwargs):
            raise ResearchUnavailable("research completion unavailable: HTTP 404 not_found_error")

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    pipeline.completion = Boom()
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status is GenerationStatus.FAILED
    assert result.status is not GenerationStatus.NO_FIT
    assert result.stop_reason == "research completion unavailable: HTTP 404 not_found_error"
    assert pipeline.last_run["status"] == "failed"
    assert pipeline.last_run["stop_reason"] == "research completion unavailable: HTTP 404 not_found_error"
    assert pipeline.last_run["failed_stage"] == "business_analysis"
    assert pipeline.last_run["usage"]["queries"] == 0
    assert pipeline.last_run["usage"]["llm_calls"] == 1
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
    assert pipeline.last_run["people_queries"] >= 1
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


def test_schema_validation_before_search_is_failed_not_no_fit(tmp_path):
    from evorove_lead.engine import GenerationStatus
    from evorove_lead.llm_usage import CompletionReply, UsageRow
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    class InvalidDNA(Completion):
        def complete_bounded(self, *, system, user, max_tokens, effort=None, **kwargs):
            if "Understand the business" in system:
                return CompletionReply('{"schema_version": 1, "secret": "LEAKED_VALUE"}', 10, 4)
            return super().complete_bounded(system=system, user=user, max_tokens=max_tokens, effort=effort)

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    pipeline.completion = InvalidDNA()
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status is GenerationStatus.FAILED
    assert result.status is not GenerationStatus.NO_FIT
    assert result.stop_reason.startswith("ValidationError BusinessDNAModelDraft")
    assert pipeline.last_run["status"] == "failed"
    assert pipeline.last_run["failed_stage"] == "business_analysis"
    assert pipeline.last_run["usage"]["queries"] == 0
    assert pipeline.last_run["usage"]["llm_calls"] == 2
    assert pipeline.last_run["validation"]["schema"] == "BusinessDNAModelDraft"
    assert pipeline.last_run["validation"]["error_count"] >= 1
    dumped = json.dumps(pipeline.last_run)
    assert "LEAKED_VALUE" not in dumped
    with Session(db) as session:
        rows = [row.payload for row in session.scalars(select(UsageRow))]
    assert any(row.get("status") == "validation_failed" for row in rows)
    assert "LEAKED_VALUE" not in json.dumps(rows)
    assert pipeline.client.queries == []
    assert not crm.accepted and store.pending_count() == 0


def test_invented_quote_is_still_rejected_and_not_no_fit(tmp_path):
    from evorove_lead.engine import GenerationStatus
    from evorove_lead.llm_usage import CompletionReply

    class InventedQuote(Completion):
        def complete_bounded(self, *, system, user, max_tokens, effort=None, **kwargs):
            if "Understand the business" in system:
                data = json.loads(user) if "previous_output_invalid" not in user else json.loads(user)["data"]
                source_id = data["sources"][0]["id"]
                claim = {
                    "id": "svc", "subject": "a", "predicate": "offers", "text": "invented offer",
                    "assertion_type": "BUSINESS_CLAIM", "confidence": 0.8,
                    "confidence_reason": "model invention",
                    "sources": [{"source_id": source_id, "quote": "INVENTED_QUOTE_SECRET", "relationship": "supports"}],
                    "slots": ["service", "geography", "customer_category"],
                }
                body = {"business_id": data["business_id"], "site_url": data["site_url"], "version": data["version"],
                        "service_country": "US", "evidence": {"business_id": data["business_id"], "subject": "a", "claims": [claim]}}
                return CompletionReply(json.dumps(body), 20, 20)
            return super().complete_bounded(system=system, user=user, max_tokens=max_tokens, effort=effort)

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    pipeline.completion = InventedQuote()
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status is GenerationStatus.FAILED
    assert result.status is not GenerationStatus.NO_FIT
    assert result.stop_reason.startswith("ValidationError BusinessDNA")
    assert pipeline.last_run["usage"]["llm_calls"] == 1
    assert "INVENTED_QUOTE_SECRET" not in json.dumps(pipeline.last_run)
    assert pipeline.client.queries == []


def test_qualification_uses_qualify_effort_other_stages_stay_low(tmp_path):
    class Recording(Completion):
        def __init__(self):
            super().__init__()
            self.efforts = []
        def complete_bounded(self, *, system, user, max_tokens, effort=None, **kwargs):
            task = system.split("Task: ", 1)[1].split(". Schema:", 1)[0]
            self.efforts.append((task, effort, max_tokens))
            return super().complete_bounded(system=system, user=user, max_tokens=max_tokens, effort=effort)
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    recorder = Recording()
    pipeline.completion = recorder
    engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    by_prefix = {task.split(".", 1)[0]: (effort, tokens) for task, effort, tokens in recorder.efforts}
    assert by_prefix["Understand the business"] == ("low", 4000)
    assert by_prefix["Plan research questions about demand, alternatives and barriers for this business"] == ("low", 2000)
    assert by_prefix["Interpret retrieved market evidence"] == ("low", 8000)
    assert by_prefix["Extract claims and a real appropriate person from the supplied sources"] == ("low", 4000)
    assert by_prefix["Assess six independent dimensions"] == ("medium", 4000)
    assert by_prefix["Independently challenge qualification"] == ("medium", 4000)


def test_truncated_market_interpret_is_failed_not_no_fit(tmp_path):
    from evorove_lead.engine import GenerationStatus
    from evorove_lead.market_intelligence import TruncatedStructuredCompletion

    class TruncateInterpret(Completion):
        def complete_bounded(self, *, system, user, max_tokens, effort=None, **kwargs):
            if "Interpret retrieved market" in system:
                raise TruncatedStructuredCompletion({
                    "stop_reason": "max_tokens", "input_tokens": 94171, "output_tokens": 4000,
                    "thinking_tokens": 0, "text_chars": 12, "content_block_types": {"text": 1},
                })
            return super().complete_bounded(system=system, user=user, max_tokens=max_tokens, effort=effort)

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    pipeline.completion = TruncateInterpret()
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status is GenerationStatus.FAILED
    assert result.status is not GenerationStatus.NO_FIT
    assert result.stop_reason == "truncated structured completion"
    assert pipeline.last_run["status"] == "failed"
    assert pipeline.last_run["failed_stage"] == "market_research"
    assert pipeline.last_run["people_queries"] == 0
    assert "hypothesis_generation" not in pipeline.last_run["stages"]
    assert "market_document_id" not in pipeline.last_run
    assert pipeline.client.queries
    assert not crm.accepted and store.pending_count() == 0


def test_each_allocated_hypothesis_gets_a_first_wave_when_iterations_is_one(tmp_path):
    from evorove_lead.engine import GenerationStatus
    from evorove_lead.llm_usage import CompletionReply
    from evorove_lead.research_budget import BudgetLimits

    class TwoBuyers(Completion):
        def complete_bounded(self, *, system, user, max_tokens, effort=None, **kwargs):
            if "Name buyers who would say" in system:
                data = json.loads(user)
                ref = data["dna"]["evidence"]["claims"][0]["id"]
                first = ICPHypothesis(
                    segment="small businesses", buyer_role="owner", potential_problem="intake coordination",
                    solution_mechanism="organize intake", signals=("expansion",), evidence_requirements=("explicit need",),
                    disqualifiers=("closed",), geography="US", search_strategy=("request",),
                    attractiveness_assumptions=("unknown",), confidence=.5, supporting_claims=(ref,),
                )
                second = first.model_copy(update={"segment": "local repair shops", "potential_problem": "slow follow up"})
                return CompletionReply(json.dumps({"hypotheses": [first.model_dump(mode="json"), second.model_dump(mode="json")]}), 20, 20)
            return super().complete_bounded(system=system, user=user, max_tokens=max_tokens, effort=effort)

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    pipeline.completion = TwoBuyers()
    pipeline.limits = BudgetLimits(iterations=1)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert result.status is not GenerationStatus.BUDGET_EXHAUSTED
    assert pipeline.last_run["status"] != "budget_exhausted"
    assert pipeline.last_run["stop_reason"] != "iterations"
    assert pipeline.last_run["people_queries"] == 2
    assert pipeline.last_run["usage"]["iterations"] == 2
    assert len(pipeline.last_run.get("people_search") or []) == 2
    assert all(row["query"] and '"' not in row["query"] for row in pipeline.last_run["people_search"])
    assert {row["hits"] for row in pipeline.last_run["people_search"]} == {1}
    assert len(pipeline.store.list("a", "icp_proposed")) == 2


def test_empty_people_search_records_query_and_zero_hits(tmp_path):
    from evorove_lead.research_budget import BudgetLimits

    class Silent(Search):
        def search(self, query):
            if query == "market demand evidence":
                return super().search(query)
            self.queries.append(query)
            return ()

    db, pipeline, engine, crm, store = setup_pipeline(tmp_path, dry_run=True)
    pipeline.client = Silent()
    pipeline.limits = BudgetLimits(iterations=1)
    engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    attempts = pipeline.last_run.get("people_search") or []
    assert attempts
    assert attempts[0]["query"] == "small businesses intake coordination US"
    assert attempts[0]["hits"] == 0
    assert attempts[0]["error"] is None
    assert attempts[0]["rejections"] == {}
