"""Cycle 1 to CRM Cold, with a stub search and a captured CRM POST.

No live network. The HTTP client is the real `HttpCrmLeadTouchSink`. The
CRM schema checked here is `LeadTouchRequest` from Evorove_CRM
(`src/api/schemas.py`): extra fields forbidden, assembled touches need a
phone or email, summary at most 500 characters.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from evorove_lead.business import BusinessSeed
from evorove_lead.crm_touch import HttpCrmLeadTouchSink, OutboxCrmLeadTouchSink, redeliver_pending
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.pattern_library import NullPatternLibrary
from evorove_lead.presence import page_material
from evorove_lead.search import PeopleHit, TraceFinding
from evorove_lead.sqlalchemy_models import CrmDeliveryRow
from evorove_lead.sqlalchemy_warehouse import SqlAlchemyCrmDeliveryStore, create_all
from evorove_lead.warehouse import RecordingAnalysisWarehouse

BAKERY_HTML = """
<html>
  <head><title>Sunrise Bakery</title></head>
  <body>
    <h1>Weekend catering for local events</h1>
    <p>We cook for busy parents in town.</p>
  </body>
</html>
"""
SITE = "https://sunrise-bakery.example/"
BUSINESS_ID = "tenant-a"
SECRET = "local_development_only"
REASON = "Looking for weekend catering for a local event."
SOURCE = "https://forum.example/jordan"

MESSAGE_KEYS = frozenset({"message", "message_text", "sms_body", "subject", "body", "draft"})


class _Identity(BaseModel):
    """Mirror of Evorove_CRM `LeadTouchIdentityRequest`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    phone: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    email: Annotated[str | None, Field(min_length=1, max_length=320)] = None


class _LeadTouchRequest(BaseModel):
    """Mirror of Evorove_CRM `LeadTouchRequest` (extra forbidden)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    touch_id: Annotated[str, Field(min_length=1, max_length=255)]
    cycle: Literal[1, 2, 3]
    kind: Literal[
        "assembled",
        "reason_updated",
        "discarded",
        "dialogue_started",
        "message",
        "offer_sent",
        "ready_to_book",
        "stopped",
        "human_takeover",
        "booked",
        "paid",
        "command_applied",
    ]
    source: Literal["evorove_lead", "evorove", "evorove_crm"]
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    person_id: Annotated[str | None, Field(min_length=8, max_length=128)] = None
    identity: _Identity = _Identity()
    identity_blob: Annotated[str | None, Field(min_length=1, max_length=500)] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str | None = None
    schema_version: Literal["1"] = "1"


class _CrmCapture(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "secret": self.headers.get("X-Internal-Task-Secret"),
                "body": json.loads(raw.decode("utf-8")),
                "status": 500 if self.server.fail_remaining else 200,  # type: ignore[attr-defined]
            }
        )
        if self.server.fail_remaining:  # type: ignore[attr-defined]
            self.server.fail_remaining -= 1  # type: ignore[attr-defined]
            self.send_response(500)
            self.end_headers()
            return
        payload = json.dumps({"duplicate": False}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        return None


class _CaptureServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CrmCapture)
        self.requests: list[dict[str, object]] = []
        self.fail_remaining = 0


@pytest.fixture()
def crm_server():
    server = _CaptureServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _finding(hit: PeopleHit) -> TraceFinding:
    return TraceFinding(
        url=hit.observed_source,
        raw_text=hit.observed_fact,
        query_used="weekend catering",
        source_channel="web_search",
        hit=hit,
    )


class _FixtureSearch:
    """Returns the fixture batch once, then nothing. No socket."""

    connected = True

    def __init__(self, findings: tuple[TraceFinding, ...]) -> None:
        self._findings = findings
        self.calls = 0

    def find(self, hypothesis):  # noqa: ANN001
        del hypothesis
        self.calls += 1
        if self.calls == 1:
            return self._findings
        return ()


class _Page:
    def load(self, seed):  # noqa: ANN001
        assert seed.site_url == SITE
        return (page_material(SITE, BAKERY_HTML),)


def _hits() -> tuple[TraceFinding, ...]:
    return (
        _finding(
            PeopleHit(
                identity="Jordan Lee, jordan@example-bakery.com",
                observed_fact=REASON,
                observed_source=SOURCE,
                channel="email",
            )
        ),
        _finding(
            PeopleHit(
                identity="sam@example.com",
                observed_fact="",
                observed_source="https://forum.example/empty",
                channel="email",
            )
        ),
        _finding(
            PeopleHit(
                identity="Alex Kim",
                observed_fact=REASON,
                observed_source=SOURCE,
                channel="email",
            )
        ),
        _finding(
            PeopleHit(
                identity="pat@example.com",
                observed_fact="Posted a photo of a dog in a park.",
                observed_source="https://photos.example/pat",
                channel="email",
            )
        ),
    )


def _engine(sink, search: _FixtureSearch) -> LeadGenerationEngine:
    return LeadGenerationEngine(
        presence=_Page(),
        hypothesis_search=search,
        lead_touch_sink=sink,
        warehouse=RecordingAnalysisWarehouse(),
        pattern_library=NullPatternLibrary(),
    )


def _assert_cold_touch(body: dict[str, Any]) -> None:
    parsed = _LeadTouchRequest.model_validate(body)
    assert parsed.schema_version == "1"
    assert parsed.touch_id.startswith("cycle1:assembled:ppl_")
    assert parsed.cycle == 1
    assert parsed.kind == "assembled"
    assert parsed.source == "evorove_lead"
    assert parsed.payload["reason"] == REASON
    assert parsed.payload["reason_source"] == SOURCE
    assert parsed.payload["channel"] == "email"
    assert parsed.payload["segment"]
    identity = parsed.identity
    assert identity.email or identity.phone
    assert identity.email == "jordan@example-bakery.com"
    blob = json.dumps(body)
    assert "Dear " not in blob
    assert MESSAGE_KEYS.isdisjoint(body)
    assert MESSAGE_KEYS.isdisjoint(body["payload"])


def test_cycle1_posts_one_cold_touch_and_redelivers_without_a_duplicate(crm_server, tmp_path) -> None:
    host, port = crm_server.server_address
    http = HttpCrmLeadTouchSink(f"http://{host}:{port}", SECRET)
    database = create_engine(f"sqlite+pysqlite:///{tmp_path / 'warehouse.db'}", future=True)
    create_all(database)
    store = SqlAlchemyCrmDeliveryStore(database)
    sink = OutboxCrmLeadTouchSink(http, store)
    crm_server.fail_remaining = 1

    first = _engine(sink, _FixtureSearch(_hits())).generate(
        BusinessSeed(site_url=SITE, business_id=BUSINESS_ID)
    )

    assert first.status is GenerationStatus.PEOPLE_FOUND
    assert first.sent_messages == ()
    assert len(first.candidates) == 1
    with Session(database) as session:
        queued = session.scalars(select(CrmDeliveryRow)).all()
    assert len(queued) == 1
    assert queued[0].touch_id.startswith("cycle1:assembled:ppl_")
    assert queued[0].attempts == 1
    failed = crm_server.requests[0]
    assert failed["status"] == 500
    assert failed["path"] == f"/api/v1/internal/businesses/{BUSINESS_ID}/lead-touches"
    assert failed["secret"] == SECRET

    redelivered, pending = redeliver_pending(http, store)
    assert (redelivered, pending) == (1, 0)
    assert store.pending() == ()
    accepted = crm_server.requests[1]["body"]
    _assert_cold_touch(accepted)
    assert accepted["touch_id"] == queued[0].touch_id
    assert accepted == queued[0].payload

    again = _engine(sink, _FixtureSearch(_hits())).generate(
        BusinessSeed(site_url=SITE, business_id=BUSINESS_ID)
    )
    assert again.status is GenerationStatus.PEOPLE_FOUND
    replay = crm_server.requests[2]["body"]
    _assert_cold_touch(replay)
    assert replay["touch_id"] == accepted["touch_id"]
    assert replay["person_id"] == accepted["person_id"]

    # The accepted touch is not sitting in the outbox, and flushing again
    # does not POST a second copy of it.
    assert redeliver_pending(http, store) == (0, 0)
    assert len(crm_server.requests) == 3
    with Session(database) as session:
        assert session.scalars(select(CrmDeliveryRow)).all() == []
