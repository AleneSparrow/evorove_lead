"""CRM writes need an explicit rehearsal or live mode."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from evorove_lead.api import create_app
from evorove_lead.client_zero import preflight
from evorove_lead.crm_touch import (
    CrmDeliveryError,
    HttpCrmLeadTouchSink,
    OutboxCrmLeadTouchSink,
    RecordingCrmDeliveryStore,
    crm_delivery_requested,
    crm_delivery_blocked_reason,
    flush_deliveries,
    parse_accept_receipt,
)


NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
PAYLOAD = {
    "schema_version": "1",
    "touch_id": "cycle1:assembled:ppl_abc",
    "person_id": "ppl_abc",
    "cycle": 1,
    "kind": "assembled",
    "source": "evorove_lead",
    "summary": "Posted looking for weekend catering.",
    "identity": {"name": "Jordan Lee", "phone": None, "email": "jordan@bakery.example"},
    "payload": {"hypothesis_id": "hyp_1"},
}


class _UrlOpen:
    def __init__(self, body: bytes, *, error=None, status: int = 200):
        self.body, self.error, self.status = body, error, status
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        raw = self.body

        class _Resp:
            def __enter__(inner):
                return inner

            def __exit__(inner, *args):
                return None

            def read(inner, n=-1):
                return raw

        resp = _Resp()
        resp.status = self.status
        return resp


def test_http_and_outbox_stay_still_unless_both_live_flags(monkeypatch):
    opened = _UrlOpen(b"{}")
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", opened)
    store = RecordingCrmDeliveryStore()
    sink = OutboxCrmLeadTouchSink(HttpCrmLeadTouchSink("https://crm.example", "secret"), store, clock=lambda: NOW)
    for delivery, isolated in (("0", "0"), ("1", "0"), ("0", "1")):
        opened.requests.clear()
        monkeypatch.setenv("RESEARCH_CRM_DELIVERY", delivery)
        monkeypatch.setenv("CRM_CYCLE2_ISOLATED", isolated)
        sink.publish("tenant-a", PAYLOAD)
        assert opened.requests == []
        pending = store.pending()
        assert len(pending) == 1
        assert pending[0].attempts == 0
        assert pending[0].last_error.startswith("delivery_deferred:")
    result = flush_deliveries(HttpCrmLeadTouchSink("https://crm.example", "secret"), store)
    assert result["redelivered"] == 0
    assert result["blocked_reason"]
    assert result["deferred"] == 1
    assert len(store.pending()) == 1


def test_deferred_touch_flushes_once_after_live_ack(monkeypatch):
    store = RecordingCrmDeliveryStore()
    body = json.dumps({"person_id": "ppl_abc", "tab": "cold", "touch_id": PAYLOAD["touch_id"]}).encode()
    opened = _UrlOpen(body)
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", opened)
    http = HttpCrmLeadTouchSink("https://crm.example", "secret")
    sink = OutboxCrmLeadTouchSink(http, store, clock=lambda: NOW)
    sink.publish("tenant-a", PAYLOAD)
    sink.publish("tenant-a", PAYLOAD)
    assert len(store.pending()) == 1
    assert store.pending()[0].attempts == 0
    assert opened.requests == []
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_LIVE_WRITE_ACK", "1")
    result = flush_deliveries(http, store)
    assert result["redelivered"] == 1
    assert result["accepted_cold"] == 1
    assert result["deferred"] == 0
    assert store.pending() == ()
    assert len(opened.requests) == 1


def test_http_sink_does_not_contact_crm_without_isolation(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    opened = _UrlOpen(b"{}")
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", opened)
    sink = HttpCrmLeadTouchSink("https://crm.example", "secret")
    with pytest.raises(CrmDeliveryError, match="crm_delivery_mode_not_acknowledged"):
        sink.publish("tenant-a", PAYLOAD)
    assert opened.requests == []
    assert sink.last_receipt is None


def test_http_sink_requires_board_receipt(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    opened = _UrlOpen(b"{}")
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", opened)
    sink = HttpCrmLeadTouchSink("https://crm.example", "secret")
    with pytest.raises(CrmDeliveryError, match="crm_accept_unconfirmed"):
        sink.publish("tenant-a", PAYLOAD)
    assert len(opened.requests) == 1


def test_http_sink_records_confirmed_cold(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    body = json.dumps(
        {
            "business_id": "tenant-a",
            "person_id": "ppl_abc",
            "tab": "cold",
            "touch_id": "cycle1:assembled:ppl_abc",
            "kind": "assembled",
            "duplicate": False,
        }
    ).encode()
    opened = _UrlOpen(body)
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", opened)
    sink = HttpCrmLeadTouchSink("https://crm.example", "secret")
    sink.publish("tenant-a", PAYLOAD)
    assert sink.last_receipt is not None
    assert sink.last_receipt.confirmed_cold is True
    assert sink.last_receipt.person_id == "ppl_abc"


def test_http_sink_does_not_treat_in_work_as_confirmed_cold(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    body = json.dumps(
        {"person_id": "ppl_abc", "tab": "in_work", "touch_id": "cycle1:assembled:ppl_abc"}
    ).encode()
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", _UrlOpen(body))
    sink = HttpCrmLeadTouchSink("https://crm.example", "secret")
    sink.publish("tenant-a", PAYLOAD)
    assert sink.last_receipt.confirmed_cold is False
    assert sink.last_receipt.tab == "in_work"


def test_flush_without_isolation_does_not_increment_attempts_or_post(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    store = RecordingCrmDeliveryStore()
    store.record_failure("tenant-a", PAYLOAD["touch_id"], PAYLOAD, "down", NOW)
    opened = _UrlOpen(b"{}", error=URLError("should not run"))
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", opened)
    http = HttpCrmLeadTouchSink("https://crm.example", "secret")
    result = flush_deliveries(http, store)
    assert result == {
        "redelivered": 0,
        "pending": 1,
        "deferred": 0,
        "accepted_cold": 0,
        "blocked_reason": "crm_delivery_mode_not_acknowledged",
    }
    assert opened.requests == []
    assert store.pending()[0].attempts == 1


def test_flush_counts_confirmed_cold_separately_from_handoffs(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    store = RecordingCrmDeliveryStore()
    store.record_failure("tenant-a", PAYLOAD["touch_id"], PAYLOAD, "down", NOW)
    other = dict(PAYLOAD)
    other["touch_id"] = "cycle1:assembled:ppl_zzz"
    store.record_failure("tenant-b", other["touch_id"], other, "down", NOW)
    bodies = [
        json.dumps({"person_id": "ppl_abc", "tab": "cold", "touch_id": PAYLOAD["touch_id"]}).encode(),
        json.dumps({"person_id": "ppl_zzz", "tab": "in_work", "touch_id": other["touch_id"]}).encode(),
    ]

    class _Queue:
        def __call__(self, request, timeout=None):
            raw = bodies.pop(0)

            class _Resp:
                status = 200

                def __enter__(inner):
                    return inner

                def __exit__(inner, *args):
                    return None

                def read(inner, n=-1):
                    return raw

            return _Resp()

    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", _Queue())
    result = flush_deliveries(HttpCrmLeadTouchSink("https://crm.example", "secret"), store)
    assert result["redelivered"] == 2
    assert result["pending"] == 0
    assert result["accepted_cold"] == 1
    assert result["blocked_reason"] == ""


def test_tenants_do_not_share_pending_or_receipts(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    store = RecordingCrmDeliveryStore()
    store.record_failure("tenant-a", "touch-a", PAYLOAD, "down", NOW)
    other = dict(PAYLOAD)
    other["touch_id"] = "touch-b"
    store.record_failure("tenant-b", "touch-b", other, "down", NOW)
    seen = []

    class _ByTenant:
        def __call__(self, request, timeout=None):
            tenant = request.full_url.split("/businesses/")[1].split("/")[0]
            seen.append(tenant)
            raw = json.dumps({"person_id": f"ppl_{tenant}", "tab": "cold", "touch_id": f"touch-{tenant[-1]}"}).encode()

            class _Resp:
                status = 200

                def __enter__(inner):
                    return inner

                def __exit__(inner, *args):
                    return None

                def read(inner, n=-1):
                    return raw

            return _Resp()

    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", _ByTenant())
    flush_deliveries(HttpCrmLeadTouchSink("https://crm.example", "secret"), store)
    assert set(seen) == {"tenant-a", "tenant-b"}
    assert store.pending() == ()


def test_research_delivery_stays_dry_without_isolation(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    assert crm_delivery_requested() is False


def test_research_delivery_needs_both_flags(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    assert crm_delivery_requested() is True


def test_live_write_requires_separate_ack_and_never_reuses_isolation(monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    monkeypatch.delenv("CRM_LIVE_WRITE_ACK", raising=False)
    assert crm_delivery_requested() is False
    assert crm_delivery_blocked_reason() == "crm_delivery_mode_not_acknowledged"

    monkeypatch.setenv("CRM_LIVE_WRITE_ACK", "1")
    assert crm_delivery_requested() is True
    assert crm_delivery_blocked_reason() == ""

    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    assert crm_delivery_requested() is False
    assert crm_delivery_blocked_reason() == "crm_delivery_mode_conflict"

    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "0")
    assert crm_delivery_requested() is False
    assert crm_delivery_blocked_reason() == "crm_delivery_not_enabled"


def test_live_ack_posts_only_when_delivery_flag_is_enabled(monkeypatch):
    monkeypatch.setenv("CRM_LIVE_WRITE_ACK", "1")
    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    opened = _UrlOpen(b'{"person_id":"ppl_abc","tab":"cold","touch_id":"cycle1:assembled:ppl_abc"}')
    monkeypatch.setattr("evorove_lead.crm_touch.urllib.request.urlopen", opened)
    sink = HttpCrmLeadTouchSink("https://crm.example", "secret")
    monkeypatch.delenv("RESEARCH_CRM_DELIVERY", raising=False)
    with pytest.raises(CrmDeliveryError, match="crm_delivery_not_enabled"):
        sink.publish("tenant-a", PAYLOAD)
    assert opened.requests == []
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    sink.publish("tenant-a", PAYLOAD)
    assert len(opened.requests) == 1
    assert sink.last_receipt is not None and sink.last_receipt.confirmed_cold


def test_preflight_reports_live_ack_without_isolation_blocker(monkeypatch):
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example")
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_LIVE_WRITE_ACK", "1")
    report = preflight()
    assert report["live_write_acknowledged"] is True
    assert report["crm_blocked_reason"] == ""
    assert not any("CRM delivery" in item for item in report["blockers"])


def test_preflight_blocks_configured_crm_without_isolation(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("RESEARCH_ENABLED", "1")
    monkeypatch.setenv("RESEARCH_ALLOW_PAID_LLM", "1")
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example")
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    monkeypatch.delenv("RESEARCH_CRM_DELIVERY", raising=False)
    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    report = preflight()
    assert report["cycle2_isolated"] is False
    assert report["crm_configured"] is True
    assert any("crm_delivery_not_enabled" in item for item in report["blockers"])


def test_parse_accept_receipt_rejects_empty_and_handoff_shaped_bodies():
    assert parse_accept_receipt(b"") is None
    assert parse_accept_receipt(b'{"ok": true}') is None
    assert parse_accept_receipt(b'{"person_id": "ppl_x"}') is None
    receipt = parse_accept_receipt(b'{"person_id": "ppl_x", "tab": "cold", "touch_id": "t1"}')
    assert receipt is not None and receipt.confirmed_cold


def test_api_flush_stays_blocked_without_isolation(monkeypatch):
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example")
    monkeypatch.delenv("CRM_CYCLE2_ISOLATED", raising=False)
    store = RecordingCrmDeliveryStore()
    store.record_failure("tenant-a", PAYLOAD["touch_id"], PAYLOAD, "down", NOW)
    client = TestClient(create_app(delivery_store=store))
    response = client.post(
        "/api/v1/internal/crm-deliveries/flush",
        headers={"X-Internal-Task-Secret": "local_development_only"},
    )
    assert response.json()["blocked_reason"] == "crm_delivery_not_enabled"
    assert store.pending()[0].attempts == 1
