"""Phase 3's inbound side: cycle 2/3's non-PII outcome event lands here.

Cycle 2 (`evorove`) and cycle 3 (`evorove-crm`) know what happened to a
person after Cold: closed, dropped, mid-conversation. They must never send
this repo who that person was -- only `hypothesis_id -> outcome`, so the
warehouse can learn which hypotheses are worth their budget (phase 4)
without any personal data crossing back into cycle 1.

Not part of `LeadGenerationEngine`. This is the only network-facing code
in this repository; run it with `uvicorn evorove_lead.api:app`.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from evorove_lead.sqlalchemy_warehouse import warehouse_from_env
from evorove_lead.warehouse import AnalysisWarehouse, HypothesisOutcomeRecord, new_id

OUTCOME_VALUES = ("done", "dropped", "offer_made", "in_progress")


class HypothesisOutcomeIn(BaseModel):
    """Non-PII by construction: no name, contact, or message text has a field here."""

    business_id: str
    hypothesis_id: str
    case_id: str
    outcome: Literal["done", "dropped", "offer_made", "in_progress"]


def _require_task_secret(provided: str | None) -> None:
    configured = os.getenv("INTERNAL_TASK_SECRET") or ""
    if not configured:
        raise HTTPException(status_code=401, detail="internal task endpoints are not enabled")
    if not provided or not hmac.compare_digest(provided, configured):
        raise HTTPException(status_code=401, detail="invalid or missing internal task secret")


def create_app(warehouse: AnalysisWarehouse | None = None) -> FastAPI:
    app = FastAPI(title="evorove_lead internal API")
    store = warehouse if warehouse is not None else warehouse_from_env()

    @app.post("/api/v1/internal/hypothesis-outcomes", status_code=202)
    def record_hypothesis_outcome(
        body: HypothesisOutcomeIn,
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        _require_task_secret(x_internal_task_secret)
        if not body.business_id.strip() or not body.hypothesis_id.strip() or not body.case_id.strip():
            raise HTTPException(status_code=422, detail="business_id, hypothesis_id, and case_id are required")
        record = HypothesisOutcomeRecord(
            id=new_id("outcome"),
            business_id=body.business_id,
            hypothesis_id=body.hypothesis_id,
            case_id=body.case_id,
            outcome=body.outcome,
            recorded_at=datetime.now(timezone.utc),
        )
        store.save_hypothesis_outcome(record)
        return {"status": "recorded", "id": record.id}

    return app


app = create_app()
