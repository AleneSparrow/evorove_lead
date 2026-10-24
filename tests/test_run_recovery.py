"""Real PostgreSQL ownership races; all product external boundaries are scripted."""
import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from test_postgres_research import pg
from evorove_lead.run_ownership import LeaseStore, RunBusy, OwnershipLost
from evorove_lead.searches import SearchTarget, SqlAlchemySearchTargetStore, claim_target, run_target, run_due
from evorove_lead.intelligence_store import IntelligenceStore
from evorove_lead.sqlalchemy_warehouse import SqlAlchemyAnalysisWarehouse, SqlAlchemyCrmDeliveryStore
from evorove_lead.sqlalchemy_models import CandidateRow, CrmDeliveryRow
from evorove_lead.research_delivery import AdmissionRow
from evorove_lead.research_budget import DailyBudgetStore, BudgetExhausted
from evorove_lead.engine import LeadGenerationEngine
from evorove_lead.research_runtime import ResearchPipeline
from evorove_lead.business import BusinessSeed
from evorove_lead.crm_touch import OutboxCrmLeadTouchSink
from intelligence_helpers import Presence, Search, Completion, fetch, NOW

pytestmark = pytest.mark.skipif(os.getenv("EVOROVE_TEST_POSTGRES") != "1", reason="isolated PostgreSQL not enabled")


def target(tenant="a"):
    return SearchTarget(tenant, "https://seller.example", "", datetime.now(timezone.utc))


def make_engine(pg, *, presence=None, search=None, crm=None):
    warehouse = SqlAlchemyAnalysisWarehouse(pg)
    pipeline = ResearchPipeline(pg, search or Search(), Completion(), fetcher=fetch, clock=lambda: NOW, dry_run=crm is None)
    kwargs = {"lead_touch_sink": OutboxCrmLeadTouchSink(crm, SqlAlchemyCrmDeliveryStore(pg))} if crm else {}
    return LeadGenerationEngine(presence=presence or Presence(), warehouse=warehouse, research_pipeline=pipeline, **kwargs)


def test_two_concurrent_claims_get_one_execution(pg):
    targets = SqlAlchemySearchTargetStore(pg)
    t = target()
    targets.save(t)
    barrier = Barrier(2)
    def claim(_):
        barrier.wait()
        try:
            return LeaseStore(pg).acquire("a", target=t, expected_last_run=None, compare_target=True)
        except RunBusy:
            return None
    with ThreadPoolExecutor(2) as pool:
        owners = [o for o in pool.map(claim, range(2)) if o]
    assert len(owners) == 1
    engine = make_engine(pg)
    assert run_target(targets, engine, t, owner=owners[0]).last_status == "people_found"
    assert len(IntelligenceStore(pg).list("a", "run")) == 1
    # A delayed scheduler snapshot cannot start again after the winner finished.
    with pytest.raises(RunBusy, match="target_already_ran"):
        claim_target(targets, engine, t)


def test_long_run_heartbeat_and_restart_recovery(pg):
    now = datetime.now(timezone.utc)
    leases = LeaseStore(pg, clock=lambda: now)
    owner = leases.acquire("a", target=target())
    docs = IntelligenceStore(pg)
    with owner.activate(heartbeat=False):
        docs.save("a", "run", owner.run_id, {"id": owner.run_id, "status": "running", "usage": {"queries": 3, "cost_microusd": 100}})
        for _ in range(20):
            now += timedelta(seconds=60)
            owner.heartbeat()
            assert not LeaseStore(pg, clock=lambda: now).recover("a")
            with pytest.raises(RunBusy):
                leases.acquire("a", target=target())
    now += timedelta(seconds=121)
    restarted = LeaseStore(pg, clock=lambda: now)
    assert restarted.recover("a")
    assert not restarted.recover("a")
    run = docs.get("a", "run", owner.run_id)
    assert run["status"] == "interrupted" and run["stop_reason"] == "heartbeat_expired"
    assert run["usage"] == {"queries": 3, "cost_microusd": 100}
    assert SqlAlchemySearchTargetStore(pg).get("a").last_status == "interrupted"
    # Cron never automatically retries interrupted work.
    assert run_due(SqlAlchemySearchTargetStore(pg), make_engine(pg), now + timedelta(days=2)) == {"ran": 0, "cold": 0}
    with pytest.raises(OwnershipLost):
        owner.heartbeat()


def test_competing_takeovers_fence_old_owner_and_preserve_other_tenant(pg):
    now = datetime.now(timezone.utc)
    leases = LeaseStore(pg, clock=lambda: now)
    old = leases.acquire("a", target=target())
    other = leases.acquire("b", target=target("b"))
    now += timedelta(seconds=121)
    barrier = Barrier(2)
    def takeover(_):
        barrier.wait()
        try:
            return LeaseStore(pg, clock=lambda: now).acquire("a", target=target())
        except RunBusy:
            return None
    with ThreadPoolExecutor(2) as pool:
        owners = [o for o in pool.map(takeover, range(2)) if o]
    assert len(owners) == 1
    new = owners[0]
    for operation in (old.heartbeat, old.check, lambda: old.finish("people_found", cold=99)):
        with pytest.raises(OwnershipLost):
            operation()
    # Even a previously activated old thread cannot save a result after takeover.
    from evorove_lead.run_ownership import CURRENT
    token = CURRENT.set(old)
    try:
        with pytest.raises(OwnershipLost):
            IntelligenceStore(pg).save("a", "run", old.run_id, {"status": "completed"})
        with pytest.raises(OwnershipLost):
            SqlAlchemySearchTargetStore(pg).save(target())
    finally:
        CURRENT.reset(token)
    assert IntelligenceStore(pg).get("a", "run_attempt", old.run_id)["status"] == "interrupted"
    assert IntelligenceStore(pg).get("a", "run_attempt", new.run_id)["previous_attempt_ids"] == [old.run_id]
    assert IntelligenceStore(pg).get("b", "run_attempt", other.run_id)["status"] == "running"
    new.finish("partial", "search_timeout", 1)
    with pytest.raises(OwnershipLost):
        old.finish("no_fit")
    assert SqlAlchemySearchTargetStore(pg).get("a").last_status == "partial"
    assert IntelligenceStore(pg).get("a", "search_status", "latest")["stop_reason"] == "search_timeout"


@pytest.mark.parametrize("failure,status,reason", [(RuntimeError("external failure"), "failed", "dependency_failure"), (asyncio.CancelledError(), "interrupted", "cancelled"), (KeyboardInterrupt(), "interrupted", "cancelled")])
def test_exception_and_cancellation_finish_both_levels(pg, failure, status, reason):
    class BrokenPresence:
        def load(self, seed):
            raise failure
    targets = SqlAlchemySearchTargetStore(pg)
    t = target()
    targets.save(t)
    engine = make_engine(pg, presence=BrokenPresence())
    if isinstance(failure, Exception):
        run_target(targets, engine, t)
    else:
        with pytest.raises(type(failure)):
            run_target(targets, engine, t)
    assert targets.get("a").last_status == status
    docs = IntelligenceStore(pg)
    assert docs.list("a", "run")[0]["status"] == status
    assert docs.list("a", "run_attempt")[0]["stop_reason"] == reason
    assert LeaseStore(pg).describe("a") == {}
    retry = LeaseStore(pg).acquire("a", target=targets.get("a"))
    retry.finish("no_fit")


def test_hard_process_exit_without_finally_and_daily_budget_preserved(pg):
    with pg.connect() as c:
        schema = c.exec_driver_sql("select current_schema()").scalar_one()
    script = '''
import os, sys
from sqlalchemy import create_engine
from evorove_lead.run_ownership import LeaseStore
from evorove_lead.intelligence_store import IntelligenceStore
from evorove_lead.searches import SearchTarget
from evorove_lead.research_budget import DailyBudgetStore
from datetime import datetime, timezone
engine = create_engine("postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_test", connect_args={"options": "-csearch_path=" + sys.argv[1]})
owner = LeaseStore(engine).acquire("crashed", target=SearchTarget("crashed", "https://seller.example", "", datetime.now(timezone.utc)))
with owner.activate(heartbeat=False):
    DailyBudgetStore(engine).reserve("crashed", "test-day", 100, 150)
    IntelligenceStore(engine).save("crashed", "run", owner.run_id, {"id": owner.run_id, "status": "running", "usage": {"cost_microusd": 100}})
    os._exit(23)
'''
    result = subprocess.run([sys.executable, "-c", script, schema], env={"PATH": os.environ["PATH"], "PYTHONPATH": str(Path("src").resolve()), "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 23, result.stderr
    targets = SqlAlchemySearchTargetStore(pg)
    assert targets.get("crashed").last_status == "running"
    future = datetime.now(timezone.utc) + timedelta(seconds=121)
    assert LeaseStore(pg, clock=lambda: future).recover("crashed")
    assert targets.get("crashed").last_status == "interrupted"
    with pytest.raises(BudgetExhausted):
        DailyBudgetStore(pg).reserve("crashed", "test-day", 100, 150)
    assert IntelligenceStore(pg).list("crashed", "run")[0]["usage"]["cost_microusd"] == 100


def test_retry_uses_atomic_admission_and_pending_outbox(pg):
    from evorove_lead.crm_touch import CrmDeliveryError
    class CRM:
        def __init__(self): self.calls = 0
        def publish(self, *args):
            self.calls += 1
            raise CrmDeliveryError("scripted outage")
    crm = CRM()
    engine = make_engine(pg, crm=crm)
    targets = SqlAlchemySearchTargetStore(pg)
    targets.save(target())
    first = run_target(targets, engine, targets.get("a"))
    assert first.last_cold == 1
    # Another attempt dies after admission; recovery permits an explicit retry.
    now = datetime.now(timezone.utc)
    old = LeaseStore(pg, clock=lambda: now).acquire("a", target=first)
    now += timedelta(seconds=121)
    assert LeaseStore(pg, clock=lambda: now).recover("a")
    run_target(targets, engine, targets.get("a"))
    assert crm.calls == 1
    with Session(pg) as session:
        for table in (CandidateRow, AdmissionRow, CrmDeliveryRow):
            assert session.scalar(select(func.count()).select_from(table)) == 1
    assert len(IntelligenceStore(pg).list("a", "run_attempt")) == 3


def test_legacy_running_requires_operator_confirmation(pg):
    targets = SqlAlchemySearchTargetStore(pg)
    from dataclasses import replace
    targets.save(replace(target(), last_status="running"))
    leases = LeaseStore(pg)
    assert not leases.recover("a")
    with pytest.raises(RunBusy, match="legacy_running_unverified"):
        leases.acquire("a", target=target())
    assert leases.describe("a")["stop_reason"] == "legacy_running_unverified"
    assert leases.recover("a", legacy_workers_stopped=True)
    assert targets.get("a").last_status == "interrupted"
    owner = leases.acquire("a", target=targets.get("a"))
    # This confirmation never overrides a modern active owner.
    assert not leases.recover("a", legacy_workers_stopped=True)
    owner.finish("no_fit")


def test_api_cli_and_scheduler_share_owner(pg, monkeypatch):
    from evorove_lead.api import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "synthetic-test-only")
    entered, release = Event(), Event()
    class BlockingPresence(Presence):
        calls = 0
        def load(self, seed):
            self.calls += 1
            entered.set()
            assert release.wait(10)
            return super().load(seed)
    presence = BlockingPresence()
    engine = make_engine(pg, presence=presence)
    targets = SqlAlchemySearchTargetStore(pg)
    app = create_app(engine._warehouse, targets=targets, engine_factory=lambda: engine)
    body = {"business_id": "a", "site_url": "https://seller.example"}
    headers = {"X-Internal-Task-Secret": "synthetic-test-only"}
    with ThreadPoolExecutor(2) as pool:
        request = pool.submit(lambda: TestClient(app).post("/api/v1/internal/searches", json=body, headers=headers))
        try:
            assert entered.wait(5)
            duplicate = TestClient(app).post("/api/v1/internal/searches", json=body, headers=headers)
            assert duplicate.json()["status"] == "running"
            assert duplicate.json()["stop_reason"] == "run_already_owned"
            assert engine.generate(BusinessSeed(site_url=body["site_url"], business_id="a")).stop_reason == "run_already_owned"
            assert run_due(targets, engine) == {"ran": 0, "cold": 0}
            status = TestClient(app).get("/api/v1/internal/searches/a", headers=headers).json()
            assert status["attempt_id"] and status["heartbeat_at"] and status["lease_expires_at"]
        finally:
            release.set()
        assert request.result(timeout=10).status_code == 202
    assert presence.calls == 1
    assert targets.get("a").last_status == "people_found"
