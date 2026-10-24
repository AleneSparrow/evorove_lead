"""Opt-in isolated PostgreSQL tests. Never read production DATABASE_URL."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine, inspect, text, select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from alembic import command
from alembic.config import Config
from evorove_lead.sqlalchemy_models import Base, CandidateRow, CrmDeliveryRow
from evorove_lead.intelligence_store import IntelligenceStore, ResearchClaimRow, ClaimSourceRow
from evorove_lead.research_budget import DailyBudgetStore, BudgetExhausted
from evorove_lead.research_delivery import ResearchDelivery, AdmissionRow
from evorove_lead.sqlalchemy_warehouse import SqlAlchemyAnalysisWarehouse
from evorove_lead.warehouse import BriefRecord, HypothesisRecord, CandidateRecord

pytestmark = pytest.mark.skipif(os.getenv("EVOROVE_TEST_POSTGRES") != "1", reason="isolated PostgreSQL not enabled")

@pytest.fixture
def pg(monkeypatch):
    # This fixed local test-only endpoint contains no credentials/secrets.
    engine = create_engine("postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_test")
    schema = "test_" + uuid.uuid4().hex
    with engine.begin() as connection: connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = create_engine("postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_test", connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(scoped)
    yield scoped
    scoped.dispose()
    with engine.begin() as connection: connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()

def test_postgres_concurrent_daily_limit(pg):
    store = DailyBudgetStore(pg)
    def reserve(_):
        try: store.reserve("a", "2026-10-09", 1, 3); return True
        except BudgetExhausted: return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(12))) == 3

def test_postgres_cross_tenant_evidence_fk(pg):
    with Session(pg) as session, session.begin():
        session.add(ResearchClaimRow(business_id="a", id="c", subject="x", payload={}))
    with pytest.raises(IntegrityError):
        with Session(pg) as session, session.begin():
            session.add(ClaimSourceRow(business_id="b", claim_id="c", source_id="s", relationship="supports", quote="q"))

def test_postgres_concurrent_admission_and_crash_pending(pg):
    now = datetime.now(timezone.utc)
    warehouse = SqlAlchemyAnalysisWarehouse(pg)
    warehouse.save_brief(BriefRecord("b", "a", (), (), (), (), now))
    warehouse.save_hypothesis(HypothesisRecord("h", "a", "b", "SMB", "web", "need", "need_statement", "live", 0, 0, 0, now))
    delivery = ResearchDelivery(pg)
    def admit(i):
        candidate = CandidateRecord(str(i), "a", "h", "jane@buyer.example", "email", "published need", "https://buyer.example", 1, 1, True, "cold", now)
        return delivery.admit(candidate, {"touch_id": "stable-touch"}, "stable-hyp", "cohort")
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(admit, range(10))) == 1
    # A process stopping after commit leaves exactly one durable delivery.
    with Session(pg) as session:
        assert session.scalar(select(func.count()).select_from(CandidateRow)) == 1
        assert session.scalar(select(func.count()).select_from(CrmDeliveryRow)) == 1
        assert session.scalar(select(func.count()).select_from(AdmissionRow)) == 1

def test_postgres_migration_roundtrip(pg, monkeypatch):
    # Exercise Alembic itself on an empty schema; no existing database is touched.
    Base.metadata.drop_all(pg)
    from evorove_lead import sqlalchemy_warehouse
    monkeypatch.setattr(sqlalchemy_warehouse, "database_url_from_environment", lambda: str(pg.url))
    config = Config("alembic.ini")
    config.attributes["connection"] = pg.connect()
    try:
        command.upgrade(config, "head")
        with pg.connect() as connection:
            assert connection.execute(text("select version_num from alembic_version")).scalar() == "0012"
        names = set(inspect(pg).get_table_names())
        assert {"lead_qualifications", "lead_feedback", "llm_usage"} <= names
        assert {"research_documents", "research_budget_days", "research_run_leases"} <= names
        warehouse = SqlAlchemyAnalysisWarehouse(pg)
        now = datetime.now(timezone.utc)
        warehouse.save_brief(BriefRecord("legacy-b", "legacy-tenant", (), (), (), (), now))
        warehouse.save_hypothesis(HypothesisRecord("legacy-h", "legacy-tenant", "legacy-b", "SMB", "web", "need", "need_statement", "live", 0, 0, 0, now))
        warehouse.save_candidate(CandidateRecord("legacy-c", "legacy-tenant", "legacy-h", "test@example.com", "email", "legacy reason", "https://example.com", 1, 1, True, "cold", now))
        from evorove_lead.sqlalchemy_warehouse import SqlAlchemyCrmDeliveryStore
        deliveries = SqlAlchemyCrmDeliveryStore(pg)
        deliveries.record_failure("legacy-tenant", "legacy-touch", {"touch_id": "legacy-touch"}, "test outage", now)
        command.downgrade(config, "0008")
        command.upgrade(config, "head")
        with Session(pg) as session:
            candidate = session.get(CandidateRow, "legacy-c")
            assert candidate.reason == "legacy reason" and candidate.rejection_reason == ""
            assert session.scalar(select(func.count()).select_from(CandidateRow)) == 1
        assert deliveries.pending()[0].touch_id == "legacy-touch"
    finally: config.attributes["connection"].close()

def test_complete_pipeline_on_postgres_with_scripted_external_services(pg):
    from intelligence_helpers import Presence, Search, Completion, fetch, NOW
    from evorove_lead.engine import LeadGenerationEngine
    from evorove_lead.business import BusinessSeed
    from evorove_lead.research_runtime import ResearchPipeline
    from evorove_lead.research_delivery import CandidateClaimRow
    from evorove_lead.crm_touch import OutboxCrmLeadTouchSink
    from evorove_lead.sqlalchemy_warehouse import SqlAlchemyCrmDeliveryStore
    class CRM:
        def __init__(self): self.payloads = []
        def publish(self, business_id, payload): self.payloads.append(payload)
    crm = CRM()
    warehouse = SqlAlchemyAnalysisWarehouse(pg)
    pipeline = ResearchPipeline(pg, Search(), Completion(), fetcher=fetch, clock=lambda: NOW, dry_run=False)
    engine = LeadGenerationEngine(presence=Presence(), warehouse=warehouse, lead_touch_sink=OutboxCrmLeadTouchSink(crm, SqlAlchemyCrmDeliveryStore(pg)), research_pipeline=pipeline)
    result = engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    assert len(result.handoffs) == len(crm.payloads) == 1, pipeline.last_run
    with Session(pg) as session:
        assert session.scalar(select(func.count()).select_from(CandidateClaimRow)) == 1
        assert session.scalar(select(func.count()).select_from(CrmDeliveryRow)) == 0
