"""Phase 3's inbound side: cycle 2/3's non-PII outcome event lands here.

Cycle 2 (`evorove`) and cycle 3 (`evorove-crm`) know what happened to a
person after Cold: closed, dropped, mid-conversation. They must never send
this repo who that person was -- only `hypothesis_id -> outcome`, so the
warehouse can learn which hypotheses are worth their budget (phase 4)
without any personal data crossing back into cycle 1.

Step 20 adds the search trigger: the CRM asks this service to search for
one business (`POST /searches`), reads the status (`GET /searches/{id}`),
and a cron re-runs every remembered site daily (`POST /searches/run-due`).

Not part of `LeadGenerationEngine`. This is the only network-facing code
in this repository; run it with `uvicorn evorove_lead.api:app`.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from evorove_lead.presence import PresenceRejected, validate_public_http_url
from evorove_lead.searches import (
    EngineFactory,
    SearchTarget,
    SearchTargetStore,
    live_engine,
    run_due,
    run_target,
    search_targets_from_env,
)

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


class SearchRequestIn(BaseModel):
    business_id: str = Field(min_length=1, max_length=128)
    site_url: str = Field(min_length=1, max_length=2048)
    business_archetype: str = Field(default="", max_length=128)


def _target_view(target: SearchTarget) -> dict[str, object]:
    return {
        "business_id": target.business_id,
        "site_url": target.site_url,
        "status": target.last_status,
        "last_run_at": target.last_run_at.isoformat() if target.last_run_at else None,
        "cold": target.last_cold,
    }


def create_app(
    warehouse: AnalysisWarehouse | None = None,
    *,
    targets: SearchTargetStore | None = None,
    engine_factory: EngineFactory | None = None,
) -> FastAPI:
    app = FastAPI(title="evorove_lead internal API")
    store = warehouse if warehouse is not None else warehouse_from_env()
    search_targets = targets if targets is not None else search_targets_from_env()
    make_engine = engine_factory or live_engine

    def _engine_or_503():
        engine = make_engine()
        if engine is None:
            raise HTTPException(status_code=503, detail="people search is not connected (WEB_SEARCH_BASE_URL)")
        return engine

    @app.post("/api/v1/internal/searches", status_code=202)
    def start_search(
        body: SearchRequestIn,
        background: BackgroundTasks,
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _require_task_secret(x_internal_task_secret)
        try:
            site_url = validate_public_http_url(body.site_url)
        except PresenceRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        engine = _engine_or_503()
        existing = search_targets.get(body.business_id)
        if existing is not None and existing.last_status == "running":
            return _target_view(existing)
        target = SearchTarget(
            business_id=body.business_id,
            site_url=site_url,
            business_archetype=body.business_archetype,
            created_at=existing.created_at if existing else datetime.now(timezone.utc),
            last_run_at=existing.last_run_at if existing else None,
            last_status="queued",
            last_cold=existing.last_cold if existing else 0,
        )
        search_targets.save(target)
        background.add_task(run_target, search_targets, engine, target)
        return _target_view(target)

    @app.get("/api/v1/internal/searches/{business_id}")
    def search_status(
        business_id: str,
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _require_task_secret(x_internal_task_secret)
        target = search_targets.get(business_id)
        if target is None:
            raise HTTPException(status_code=404, detail="no search for this business yet")
        return _target_view(target)

    @app.post("/api/v1/internal/searches/run-due")
    def run_due_searches(
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, int]:
        _require_task_secret(x_internal_task_secret)
        return run_due(search_targets, _engine_or_503())

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
