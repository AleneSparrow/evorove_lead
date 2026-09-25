"""Durable CRM delivery: a failed POST is queued, retried, and counted -- never dropped silently."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from evorove_lead.api import create_app
from evorove_lead.crm_touch import (
    CrmDeliveryError,
    NullCrmDeliveryStore,
    NullLeadTouchSink,
    OutboxCrmLeadTouchSink,
    RecordingCrmDeliveryStore,
    http_sink_from_env,
    redeliver_pending,
    sink_from_env,
)
from evorove_lead.sqlalchemy_warehouse import (
    SqlAlchemyCrmDeliveryStore,
    create_all,
    crm_delivery_store_from_env,
    flush_crm_deliveries_from_env,
)

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)

PAYLOAD: dict[str, object] = {
    "schema_version": "1",
    "touch_id": "cycle1:assembled:ppl_abc",
    "person_id": "ppl_abc",
    "cycle": 1,
    "kind": "assembled",
    "source": "evorove_lead",
    "summary": "Posted looking for weekend catering.",
    "identity": {"name": "Jordan Lee", "phone": None, "email": "jordan@example-bakery.com"},
    "payload": {"hypothesis_id": "hyp_1"},
}

TOUCH_ID = "cycle1:assembled:ppl_abc"


class _FakeHttpSink:
    """Stands in for `HttpCrmLeadTouchSink`: fails `failures` times, then accepts."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.published: list[tuple[str, dict[str, object]]] = []

    def publish(self, business_id: str, payload: dict[str, object]) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise CrmDeliveryError("connection refused")
        self.published.append((business_id, payload))


class _AlwaysFailingHttpSink:
    def publish(self, business_id: str, payload: dict[str, object]) -> None:
        raise CrmDeliveryError("connection refused")


def test_failed_publish_is_queued_and_does_not_block_search(caplog) -> None:
    store = RecordingCrmDeliveryStore()
    sink = OutboxCrmLeadTouchSink(_AlwaysFailingHttpSink(), store, clock=lambda: NOW)

    with caplog.at_level(logging.WARNING, logger="evorove_lead.crm_touch"):
        sink.publish("tenant-a", PAYLOAD)  # must not raise

    pending = store.pending()
    assert len(pending) == 1
    record = pending[0]
    assert record.business_id == "tenant-a"
    assert record.touch_id == TOUCH_ID
    assert record.attempts == 1
    assert "connection refused" in record.last_error
    assert record.created_at == NOW and record.updated_at == NOW
    # The failure is visible in the log, with identifiers only -- no contact data.
    assert "crm touch not delivered" in caplog.text
    assert "@" not in caplog.text


def test_successful_publish_clears_the_queue() -> None:
    store = RecordingCrmDeliveryStore()
    http = _FakeHttpSink()
    sink = OutboxCrmLeadTouchSink(http, store, clock=lambda: NOW)
    sink.publish("tenant-a", PAYLOAD)
    # A redelivery of the same touch_id must not resurrect a queue row either.
    sink.publish("tenant-a", PAYLOAD)

    assert store.pending() == ()
    assert len(http.published) == 2


def test_record_failure_upserts_and_counts_attempts() -> None:
    store = RecordingCrmDeliveryStore()
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "first failure", NOW)
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "second failure", LATER)
    store.record_failure("tenant-b", TOUCH_ID, PAYLOAD, "other tenant", NOW)

    records = store.pending()
    assert len(records) == 2
    tenant_a = next(record for record in records if record.business_id == "tenant-a")
    assert tenant_a.attempts == 2
    assert tenant_a.last_error == "second failure"
    assert tenant_a.created_at == NOW  # first failure's timestamp is kept
    assert tenant_a.updated_at == LATER
    assert store.pending_count() == 2

    store.remove("tenant-a", TOUCH_ID)
    assert store.pending_count() == 1


def test_redeliver_pending_retries_until_crm_accepts() -> None:
    store = RecordingCrmDeliveryStore()
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "crm was down", NOW)
    http = _FakeHttpSink(failures=1)

    # One attempt per flush -- a still-failing CRM keeps the touch queued.
    assert redeliver_pending(http, store, clock=lambda: LATER) == (0, 1)
    # The next run's flush delivers it.
    assert redeliver_pending(http, store, clock=lambda: LATER) == (1, 0)
    assert http.published == [("tenant-a", PAYLOAD)]
    assert store.pending() == ()


def test_redeliver_pending_keeps_failures_queued_and_never_raises(caplog) -> None:
    store = RecordingCrmDeliveryStore()
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "crm was down", NOW)

    with caplog.at_level(logging.WARNING, logger="evorove_lead.crm_touch"):
        redelivered, still_pending = redeliver_pending(
            _AlwaysFailingHttpSink(), store, clock=lambda: LATER
        )

    assert (redelivered, still_pending) == (0, 1)
    record = store.pending()[0]
    assert record.attempts == 2
    assert "connection refused" in record.last_error
    assert "crm touch redelivery failed" in caplog.text


def test_redeliver_pending_respects_the_limit() -> None:
    store = RecordingCrmDeliveryStore()
    for index in range(3):
        store.record_failure("tenant-a", f"touch-{index}", PAYLOAD, "down", NOW)

    redelivered, still_pending = redeliver_pending(_FakeHttpSink(), store, limit=2)

    assert (redelivered, still_pending) == (2, 1)


def test_null_store_counts_zero() -> None:
    store = NullCrmDeliveryStore()
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "ignored", NOW)
    assert store.pending() == ()
    assert store.pending_count() == 0


def test_http_sink_from_env_requires_both_settings(monkeypatch) -> None:
    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    monkeypatch.delenv("INTERNAL_TASK_SECRET", raising=False)
    assert http_sink_from_env() is None

    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example/")
    assert http_sink_from_env() is None  # secret still missing

    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    sink = http_sink_from_env()
    assert sink is not None
    assert sink._base_url == "https://crm.example"  # trailing slash stripped


def test_sink_from_env_falls_back_to_null_without_crm_config(monkeypatch) -> None:
    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    monkeypatch.delenv("INTERNAL_TASK_SECRET", raising=False)
    assert isinstance(sink_from_env(), NullLeadTouchSink)


def test_sink_from_env_wraps_http_in_the_outbox(monkeypatch) -> None:
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example")
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert isinstance(sink_from_env(), OutboxCrmLeadTouchSink)


@pytest.fixture()
def sql_store(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'warehouse.db'}", future=True)
    create_all(engine)
    return SqlAlchemyCrmDeliveryStore(engine)


def test_sql_store_roundtrip(sql_store) -> None:
    assert sql_store.pending_count() == 0

    sql_store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "first failure", NOW)
    sql_store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "second failure", LATER)

    records = sql_store.pending()
    assert len(records) == 1
    record = records[0]
    assert record.business_id == "tenant-a"
    assert record.touch_id == TOUCH_ID
    assert record.attempts == 2
    assert record.last_error == "second failure"
    assert record.payload["person_id"] == "ppl_abc"
    assert sql_store.pending_count() == 1

    sql_store.remove("tenant-a", TOUCH_ID)
    assert sql_store.pending() == ()
    assert sql_store.pending_count() == 0


def test_crm_delivery_store_from_env_without_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert isinstance(crm_delivery_store_from_env(), NullCrmDeliveryStore)


def _url_store(tmp_path) -> tuple[str, SqlAlchemyCrmDeliveryStore]:
    """A DATABASE_URL + store pair whose schema actually exists on disk."""

    url = f"sqlite+pysqlite:///{tmp_path / 'warehouse.db'}"
    engine = create_engine(url, future=True)
    create_all(engine)
    return url, SqlAlchemyCrmDeliveryStore(engine)


def test_flush_counts_pending_without_crm_connection(monkeypatch, tmp_path) -> None:
    url, store = _url_store(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "down", NOW)

    assert flush_crm_deliveries_from_env() == {"redelivered": 0, "pending": 1}


def test_flush_redelivers_through_the_real_http_path(monkeypatch, tmp_path) -> None:
    url, store = _url_store(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRM_BASE_URL", "http://127.0.0.1:9")  # discard port: always refused
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "down", NOW)

    result = flush_crm_deliveries_from_env()

    assert result["redelivered"] == 0
    assert result["pending"] == 1
    assert store.pending()[0].attempts == 2  # the retry was recorded, not swallowed


SECRET_HEADER = "X-Internal-Task-Secret"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")


def _client(delivery_store=None) -> tuple[TestClient, RecordingCrmDeliveryStore]:
    store = delivery_store if delivery_store is not None else RecordingCrmDeliveryStore()
    return TestClient(create_app(delivery_store=store)), store


def test_api_status_counts_pending_deliveries() -> None:
    client, store = _client()
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "down", NOW)

    response = client.get(
        "/api/v1/internal/crm-deliveries/status",
        headers={SECRET_HEADER: "local_development_only"},
    )

    assert response.status_code == 200
    assert response.json() == {"pending": 1}


def test_api_flush_retries_queued_touches(monkeypatch) -> None:
    monkeypatch.setenv("CRM_BASE_URL", "http://127.0.0.1:9")  # discard port: always refused
    client, store = _client()
    store.record_failure("tenant-a", TOUCH_ID, PAYLOAD, "down", NOW)

    response = client.post(
        "/api/v1/internal/crm-deliveries/flush",
        headers={SECRET_HEADER: "local_development_only"},
    )

    assert response.status_code == 200
    # CRM unreachable: nothing redelivered, the failed retry stays queued and counted.
    assert response.json() == {"redelivered": 0, "pending": 1}
    assert store.pending()[0].attempts == 2


def test_api_delivery_endpoints_require_the_secret() -> None:
    client, _ = _client()

    status = client.get("/api/v1/internal/crm-deliveries/status")
    flush = client.post("/api/v1/internal/crm-deliveries/flush")

    assert status.status_code == 401
    assert flush.status_code == 401


def test_client_zero_summary_exposes_crm_delivery_counts(monkeypatch) -> None:
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from evorove_lead.client_zero import run_client_zero_search

    summary = run_client_zero_search()

    assert summary["status"] == "search_unconnected"
    assert summary["crm_pending"] == 0
    assert summary["crm_redelivered"] == 0
