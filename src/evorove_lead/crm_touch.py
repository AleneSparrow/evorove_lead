"""CRM lead-touch payloads from cycle 1 and their delivery to the CRM board.

This module talks to the CRM service, never to the found person -- cycle 1
never sends a message to a lead. Delivery is durable: a failed POST lands in
the `crm_deliveries` outbox queue, is retried on later runs, and is counted
where the owner looks. The engine is still never blocked by a CRM outage,
but the failure is no longer invisible.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

from evorove_lead.candidate import Candidate
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.warehouse import new_id

PERSON_ID_PREFIX = "ppl_"
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CrmDeliveryError(Exception):
    """POSTing one touch to CRM failed (network error, timeout, or non-2xx)."""


class LeadTouchSink(Protocol):
    def publish(self, business_id: str, payload: dict[str, object]) -> None: ...


class NullLeadTouchSink:
    def publish(self, business_id: str, payload: dict[str, object]) -> None:
        return None


class RecordingLeadTouchSink:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, object]]] = []

    def publish(self, business_id: str, payload: dict[str, object]) -> None:
        self.published.append((business_id, payload))


class HttpCrmLeadTouchSink:
    """Pure HTTP transport: POST one touch to CRM. Raises `CrmDeliveryError`."""

    def __init__(self, base_url: str, secret: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret

    def publish(self, business_id: str, payload: dict[str, object]) -> None:
        url = f"{self._base_url}/api/v1/internal/businesses/{business_id}/lead-touches"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Internal-Task-Secret": self._secret,
            },
        )
        try:
            urllib.request.urlopen(request, timeout=5).close()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CrmDeliveryError(f"POST {url} failed: {exc}") from exc


@dataclass(frozen=True)
class CrmDeliveryRecord:
    """One CRM touch still sitting in the outbox queue, waiting for CRM to accept it."""

    id: str
    business_id: str
    touch_id: str
    payload: dict[str, object]
    attempts: int
    last_error: str
    created_at: datetime
    updated_at: datetime


class CrmDeliveryStore(Protocol):
    """Durable queue of CRM touches that CRM has not accepted yet.

    A row exists only while undelivered: `record_failure` upserts by
    (business_id, touch_id), `remove` deletes once CRM accepts the touch.
    CRM dedupes replays of the same touch_id itself, so redelivery is safe.
    """

    def record_failure(
        self,
        business_id: str,
        touch_id: str,
        payload: dict[str, object],
        error: str,
        now: datetime,
    ) -> None: ...

    def pending(self) -> Sequence[CrmDeliveryRecord]: ...

    def remove(self, business_id: str, touch_id: str) -> None: ...

    def pending_count(self) -> int: ...


class NullCrmDeliveryStore:
    """No warehouse configured: failures are logged, not retried."""

    def record_failure(
        self,
        business_id: str,
        touch_id: str,
        payload: dict[str, object],
        error: str,
        now: datetime,
    ) -> None:
        return None

    def pending(self) -> Sequence[CrmDeliveryRecord]:
        return ()

    def remove(self, business_id: str, touch_id: str) -> None:
        return None

    def pending_count(self) -> int:
        return 0


class RecordingCrmDeliveryStore:
    """In-memory delivery queue for tests."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], CrmDeliveryRecord] = {}

    def record_failure(
        self,
        business_id: str,
        touch_id: str,
        payload: dict[str, object],
        error: str,
        now: datetime,
    ) -> None:
        key = (business_id, touch_id)
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = CrmDeliveryRecord(
                id=new_id("delivery"),
                business_id=business_id,
                touch_id=touch_id,
                payload=dict(payload),
                attempts=1,
                last_error=error,
                created_at=now,
                updated_at=now,
            )
        else:
            self._records[key] = CrmDeliveryRecord(
                id=existing.id,
                business_id=business_id,
                touch_id=touch_id,
                payload=dict(payload),
                attempts=existing.attempts + 1,
                last_error=error,
                created_at=existing.created_at,
                updated_at=now,
            )

    def pending(self) -> Sequence[CrmDeliveryRecord]:
        return tuple(sorted(self._records.values(), key=lambda record: record.created_at))

    def remove(self, business_id: str, touch_id: str) -> None:
        self._records.pop((business_id, touch_id), None)

    def pending_count(self) -> int:
        return len(self._records)


class OutboxCrmLeadTouchSink:
    """POST one touch to CRM; on failure queue it for retry instead of dropping it.

    Never raises: a CRM outage must not block the search run. The failure is
    recorded in the delivery store (retried by `redeliver_pending` on later
    runs) and logged with business_id/touch_id -- never contact data.
    """

    def __init__(
        self,
        http: HttpCrmLeadTouchSink,
        store: CrmDeliveryStore,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._http = http
        self._store = store
        self._clock = clock

    def publish(self, business_id: str, payload: dict[str, object]) -> None:
        touch_id = str(payload.get("touch_id") or "").strip()
        try:
            self._http.publish(business_id, payload)
        except CrmDeliveryError as exc:
            self._store.record_failure(business_id, touch_id, payload, str(exc), self._clock())
            LOGGER.warning(
                "crm touch not delivered, queued for retry: business_id=%s touch_id=%s error=%s",
                business_id,
                touch_id,
                exc,
            )
            return
        self._store.remove(business_id, touch_id)


def redeliver_pending(
    http: HttpCrmLeadTouchSink,
    store: CrmDeliveryStore,
    *,
    limit: int = 100,
    clock: Callable[[], datetime] = _utcnow,
) -> tuple[int, int]:
    """Retry queued CRM touches, oldest first. Returns (redelivered, still_pending).

    Never raises: a touch that keeps failing stays in the queue with its
    attempt count and last error bumped, so nothing is lost and nothing
    blocks the run that happens to be flushing the queue.
    """

    redelivered = 0
    for record in store.pending()[:limit]:
        try:
            http.publish(record.business_id, record.payload)
        except CrmDeliveryError as exc:
            store.record_failure(record.business_id, record.touch_id, record.payload, str(exc), clock())
            LOGGER.warning(
                "crm touch redelivery failed: business_id=%s touch_id=%s attempts=%d error=%s",
                record.business_id,
                record.touch_id,
                record.attempts + 1,
                exc,
            )
            continue
        store.remove(record.business_id, record.touch_id)
        redelivered += 1
    return redelivered, store.pending_count()


def http_sink_from_env() -> HttpCrmLeadTouchSink | None:
    """CRM connection from the environment, or None when it is not configured."""

    base = (os.getenv("CRM_BASE_URL") or "").strip().rstrip("/")
    secret = os.getenv("INTERNAL_TASK_SECRET") or ""
    if base and secret:
        return HttpCrmLeadTouchSink(base, secret)
    return None


def sink_from_env() -> LeadTouchSink:
    http = http_sink_from_env()
    if http is None:
        return NullLeadTouchSink()
    from evorove_lead.sqlalchemy_warehouse import crm_delivery_store_from_env

    return OutboxCrmLeadTouchSink(http, crm_delivery_store_from_env())


def stable_person_id(business_id: str, *, phone: str | None = None, email: str | None = None, identity: str = "") -> str:
    key = (phone or "").strip() or (email or "").strip().casefold() or identity.strip().casefold()
    digest = hashlib.sha256(f"{business_id}\n{key}".encode("utf-8")).hexdigest()[:32]
    return f"{PERSON_ID_PREFIX}{digest}"


def split_identity(blob: str) -> tuple[str | None, str | None, str | None]:
    text = (blob or "").strip()
    email = None
    match = EMAIL_RE.search(text)
    if match:
        email = match.group(0).casefold()
        text = f"{text[:match.start()]} {text[match.end():]}"
    digits = "".join(character for character in blob if character.isdigit())
    phone = digits if 7 <= len(digits) <= 15 else None
    name = re.sub(r"[\s,]+", " ", text).strip() or None
    if name and name.isdigit():
        name = None
    return name, phone, email


def assembled_touch(business_id: str, candidate: Candidate, handoff: Cycle1Handoff) -> dict[str, object]:
    name, phone, email = split_identity(candidate.identity)
    person_id = handoff.person_id or stable_person_id(
        business_id, phone=phone, email=email, identity=candidate.identity
    )
    return {
        "schema_version": "1",
        "touch_id": f"cycle1:assembled:{person_id}",
        "person_id": person_id,
        "cycle": 1,
        "kind": "assembled",
        "source": "evorove_lead",
        "summary": candidate.reason[:500],
        "identity": {"name": name, "phone": phone, "email": email},
        "identity_blob": candidate.identity,
        "payload": {
            "reason": candidate.reason,
            "reason_source": candidate.reason_source,
            "channel": handoff.channel,
            # Non-PII: lets phase 3's outcome event (Done/dropped in cycle
            # 2/3) find its way back to the hypothesis in this repo's own
            # warehouse. Empty when the bridge path (no real Hypothesis
            # yet) produced this handoff.
            "hypothesis_id": handoff.hypothesis_id,
        },
    }
