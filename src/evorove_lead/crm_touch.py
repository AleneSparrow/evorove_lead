"""CRM lead-touch payloads from cycle 1. This module does not send a message."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Protocol

from evorove_lead.candidate import Candidate
from evorove_lead.handoff import Cycle1Handoff

PERSON_ID_PREFIX = "ppl_"
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


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
    """POST one touch to CRM. Failures are swallowed so search is not blocked."""

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
        except (urllib.error.URLError, TimeoutError, OSError):
            return


def sink_from_env() -> LeadTouchSink:
    base = (os.getenv("CRM_BASE_URL") or "").strip().rstrip("/")
    secret = os.getenv("INTERNAL_TASK_SECRET") or ""
    if base and secret:
        return HttpCrmLeadTouchSink(base, secret)
    return NullLeadTouchSink()


def stable_person_id(business_id: str, *, phone: str | None = None, email: str | None = None, identity: str = "") -> str:
    key = (phone or "").strip() or (email or "").strip().casefold() or identity.strip().casefold()
    digest = hashlib.sha256(f"{business_id}\n{key}".encode("utf-8")).hexdigest()[:32]
    return f"{PERSON_ID_PREFIX}{digest}"


def split_identity(blob: str) -> tuple[str | None, str | None, str | None]:
    text = (blob or "").strip()
    email = None
    match = _EMAIL_RE.search(text)
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
        },
    }
