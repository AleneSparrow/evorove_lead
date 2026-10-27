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
import time
from datetime import datetime, timezone
from typing import Annotated, Callable, Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from evorove_lead.market_signal_llm import MarketSignalCompletion, market_signal_completion_from_env
from evorove_lead.market_signals import MarketSignalStore, market_signal_store_from_env
from evorove_lead.presence import PresenceRejected, validate_public_http_url
from evorove_lead.searches import (
    EngineFactory,
    SearchTarget,
    SearchTargetStore,
    live_engine,
    run_due,
    run_due_market_signals,
    run_target,
    claim_target,
    recover_targets,
    search_targets_from_env,
)
from evorove_lead.web_search import WebSearchClient, client_from_env

from evorove_lead.crm_touch import (
    CrmDeliveryStore, crm_cycle2_isolated, crm_delivery_blocked_reason,
    crm_live_write_acknowledged, flush_deliveries, http_sink_from_env,
)
from evorove_lead.lead_feedback import FEEDBACK_KINDS, apply_feedback
from evorove_lead.insights import register_insights_routes
from evorove_lead.sqlalchemy_warehouse import crm_delivery_store_from_env, warehouse_from_env
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


class LeadFeedbackIn(BaseModel):
    business_id: str = Field(min_length=1, max_length=128)
    qualification_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=32)


class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: list[float] = []

    def check(self) -> None:
        now = time.monotonic()
        self._hits = [stamp for stamp in self._hits if now - stamp < 60]
        if len(self._hits) >= self.per_minute:
            raise HTTPException(status_code=429, detail="rate limit")
        self._hits.append(now)


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
    delivery_store: CrmDeliveryStore | None = None,
    signal_store: MarketSignalStore | None = None,
    signal_web_search_client_factory: Callable[[], WebSearchClient | None] = client_from_env,
    signal_llm_factory: Callable[[], MarketSignalCompletion | None] = market_signal_completion_from_env,
    rate_limit_per_minute: int = 600,
) -> FastAPI:
    app = FastAPI(title="evorove_lead internal API")
    store = warehouse if warehouse is not None else warehouse_from_env()
    search_targets = targets if targets is not None else search_targets_from_env()
    deliveries = delivery_store if delivery_store is not None else crm_delivery_store_from_env()
    signals = signal_store if signal_store is not None else market_signal_store_from_env()
    make_engine = engine_factory or live_engine
    limiter = _RateLimiter(rate_limit_per_minute)
    register_insights_routes(app, store)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "evorove_lead"}

    def _limited() -> None:
        limiter.check()

    def _engine_or_503():
        from evorove_lead.web_search import SearchError
        try:
            engine = make_engine()
        except SearchError as exc:
            raise HTTPException(status_code=503, detail=exc.code) from None
        if engine is None:
            raise HTTPException(status_code=503, detail="people search is not connected (WEB_SEARCH_BASE_URL)")
        return engine

    def _market_signal_deps_or_503() -> tuple[WebSearchClient, MarketSignalCompletion]:
        from evorove_lead.web_search import SearchError
        try:
            client = signal_web_search_client_factory()
        except SearchError as exc:
            raise HTTPException(status_code=503, detail=exc.code) from None
        if client is None:
            raise HTTPException(status_code=503, detail="people search is not connected (WEB_SEARCH_BASE_URL)")
        llm = signal_llm_factory()
        if llm is None:
            raise HTTPException(
                status_code=503,
                detail="market signal reading is not enabled (AI_PROVIDER=anthropic required)",
            )
        return client, llm

    def target_view(target):
        view = _target_view(target)
        from evorove_lead.run_ownership import LeaseStore
        db = getattr(search_targets, "database_engine", None)
        if db is None:
            db = getattr(store, "database_engine", None)
        if db is not None:
            from evorove_lead.intelligence_store import IntelligenceStore
            details = IntelligenceStore(db).get(target.business_id, "search_status", "latest")
            if details and details["status"] == target.last_status and target.last_run_at and details["last_run_at"] == target.last_run_at.isoformat():
                # A finished empty search is no_fit without a transport reason.
                # Failure, partial, and budget stops keep the reason.
                if details.get("stop_reason"):
                    view["stop_reason"] = details["stop_reason"]
                if details.get("attempt_id"):
                    view["attempt_id"] = details["attempt_id"]
            view.update(LeaseStore(db).describe(target.business_id))
        return view

    @app.post("/api/v1/internal/searches", status_code=202)
    def start_search(
        body: SearchRequestIn,
        background: BackgroundTasks,
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _limited()
        _require_task_secret(x_internal_task_secret)
        try:
            site_url = validate_public_http_url(body.site_url)
        except PresenceRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        engine = _engine_or_503()
        existing = search_targets.get(body.business_id)
        target = SearchTarget(
            business_id=body.business_id,
            site_url=site_url,
            business_archetype=body.business_archetype,
            created_at=existing.created_at if existing else datetime.now(timezone.utc),
            last_run_at=existing.last_run_at if existing else None,
            last_status="queued",
            last_cold=existing.last_cold if existing else 0,
        )
        from evorove_lead.run_ownership import RunBusy
        try:
            owner = claim_target(search_targets, engine, target)
        except RunBusy as exc:
            current = search_targets.get(body.business_id)
            view = target_view(current or target)
            view["stop_reason"] = str(exc)
            return view
        background.add_task(run_target, search_targets, engine, target, owner=owner)
        return target_view(target)

    @app.get("/api/v1/internal/searches/{business_id}")
    def search_status(
        business_id: str,
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _limited()
        _require_task_secret(x_internal_task_secret)
        recover_targets(search_targets)
        target = search_targets.get(business_id)
        if target is None:
            raise HTTPException(status_code=404, detail="no search for this business yet")
        return target_view(target)

    @app.post("/api/v1/internal/searches/run-due")
    def run_due_searches(
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _limited()
        _require_task_secret(x_internal_task_secret)
        return run_due(search_targets, _engine_or_503())

    @app.post("/api/v1/internal/market-signals/run-due")
    def run_due_market_signals_route(
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        """Track 4's own daily cron entry point -- see run_due_market_signals."""

        _limited()
        _require_task_secret(x_internal_task_secret)
        client, llm = _market_signal_deps_or_503()
        return run_due_market_signals(search_targets, signals, client, llm, _engine_or_503())

    @app.get("/api/v1/internal/crm-deliveries/status")
    def crm_delivery_status(
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _limited()
        _require_task_secret(x_internal_task_secret)
        return {
            "pending": deliveries.pending_count(),
            "deferred": sum(record.last_error.startswith("delivery_deferred:") for record in deliveries.pending()),
            "cycle2_isolated": crm_cycle2_isolated(),
            "live_write_acknowledged": crm_live_write_acknowledged(),
            "blocked_reason": crm_delivery_blocked_reason(),
        }

    @app.post("/api/v1/internal/crm-deliveries/flush")
    def crm_delivery_flush(
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _limited()
        _require_task_secret(x_internal_task_secret)
        return flush_deliveries(http_sink_from_env(), deliveries)

    @app.post("/api/v1/internal/hypothesis-outcomes", status_code=202)
    def record_hypothesis_outcome(
        body: HypothesisOutcomeIn,
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        _limited()
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

    @app.post("/api/v1/internal/lead-feedback", status_code=202)
    def record_lead_feedback(
        body: LeadFeedbackIn,
        x_internal_task_secret: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _limited()
        _require_task_secret(x_internal_task_secret)
        if body.kind not in FEEDBACK_KINDS:
            raise HTTPException(status_code=422, detail="unknown feedback kind")
        saved = apply_feedback(
            store,
            business_id=body.business_id,
            qualification_id=body.qualification_id,
            kind=body.kind,
        )
        return {
            "status": "recorded",
            "id": saved.id,
            "budget_delta": saved.budget_delta,
        }

    return app


app = create_app()
