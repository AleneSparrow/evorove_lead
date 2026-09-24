"""Step 20: the CRM starts a search; a daily cron re-runs remembered sites."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from evorove_lead.api import create_app
from evorove_lead.engine import GenerationResult, GenerationStatus
from evorove_lead.searches import InMemorySearchTargetStore, SqlAlchemySearchTargetStore, SearchTarget, run_due
from evorove_lead.sqlalchemy_warehouse import create_all
from evorove_lead.warehouse import RecordingAnalysisWarehouse

HEADERS = {"X-Internal-Task-Secret": "local_development_only"}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")


class FakeEngine:
    def __init__(self, cold: int = 2, fail: bool = False) -> None:
        self.seeds = []
        self.cold, self.fail = cold, fail

    def generate(self, seed):
        self.seeds.append(seed)
        if self.fail:
            raise RuntimeError("search provider down")
        return GenerationResult(
            status=GenerationStatus.PEOPLE_FOUND if self.cold else GenerationStatus.NO_FIT,
            offer=None, candidates=(), handoffs=tuple(object() for _ in range(self.cold)), rejected=(),
        )


def _client(engine=None, targets=None):
    targets = targets if targets is not None else InMemorySearchTargetStore()
    app = create_app(RecordingAnalysisWarehouse(), targets=targets, engine_factory=lambda: engine)
    return TestClient(app), targets


def test_search_runs_for_the_business_and_reports_status() -> None:
    engine = FakeEngine(cold=3)
    client, targets = _client(engine)
    body = {"business_id": "tenant-a", "site_url": "https://acme-heating.com/"}
    assert client.post("/api/v1/internal/searches", json=body).status_code == 401
    started = client.post("/api/v1/internal/searches", json=body, headers=HEADERS)
    assert started.status_code == 202 and started.json()["status"] == "queued"
    status = client.get("/api/v1/internal/searches/tenant-a", headers=HEADERS).json()
    assert status["status"] == "people_found" and status["cold"] == 3  # background task ran after the response
    assert engine.seeds[0].site_url == "https://acme-heating.com/" and engine.seeds[0].business_id == "tenant-a"
    assert client.get("/api/v1/internal/searches/other", headers=HEADERS).status_code == 404


def test_private_urls_and_missing_search_are_refused() -> None:
    client, _ = _client(FakeEngine())
    bad = client.post("/api/v1/internal/searches", json={"business_id": "t", "site_url": "http://127.0.0.1/admin"}, headers=HEADERS)
    assert bad.status_code == 422
    unconnected, _ = _client(None)
    off = unconnected.post("/api/v1/internal/searches", json={"business_id": "t", "site_url": "https://acme.com/"}, headers=HEADERS)
    assert off.status_code == 503 and "not connected" in off.text


def test_cron_reruns_only_due_sites_and_records_failures() -> None:
    now = datetime.now(timezone.utc)
    store = InMemorySearchTargetStore()
    store.save(SearchTarget("old", "https://a.com/", "", now - timedelta(days=3), last_run_at=now - timedelta(days=1), last_status="no_fit"))
    store.save(SearchTarget("fresh", "https://b.com/", "", now, last_run_at=now - timedelta(hours=2), last_status="people_found"))
    store.save(SearchTarget("never", "https://c.com/", "", now))
    assert run_due(store, FakeEngine(cold=1), now) == {"ran": 2, "cold": 2}
    assert store.get("fresh").last_cold == 0 and store.get("old").last_status == "people_found"

    later = now + timedelta(days=2)
    assert run_due(store, FakeEngine(fail=True), later)["ran"] == 3
    assert store.get("never").last_status == "failed"


def test_sqlalchemy_store_round_trip(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'lead.db'}", future=True)
    create_all(engine)
    store = SqlAlchemySearchTargetStore(engine)
    now = datetime.now(timezone.utc)
    store.save(SearchTarget("tenant-a", "https://acme.com/", "hvac", now))
    assert store.get("tenant-a").site_url == "https://acme.com/"
    assert [t.business_id for t in store.due(now)] == ["tenant-a"]
    client, _ = _client(FakeEngine(cold=1), targets=store)
    assert client.post("/api/v1/internal/searches/run-due", headers=HEADERS).json() == {"ran": 1, "cold": 1}
    assert store.due(now) == ()
