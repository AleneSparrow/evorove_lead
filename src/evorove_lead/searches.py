"""Run cycle 1 on demand and on a schedule (roadmap step 20).

The owner pastes her site in the CRM; the CRM asks this service to search.
The site is remembered per business so a daily cron finds new people without
anyone pressing the button again. Found people land on CRM Cold through the
same engine and handoff as the CLI (`run.py`). Nothing here writes to a
person.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from threading import RLock
from evorove_lead.run_ownership import LeaseStore, RunOwner, RunBusy, OwnershipLost, CURRENT, guarded_call
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol, Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from evorove_lead.business import BusinessSeed
from evorove_lead.decision_maker_llm import decision_maker_completion_from_env
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.hypothesis import GeoRadius
from evorove_lead.marketing_analysis import marketing_analyzer_from_env
from evorove_lead.market_signal_llm import MarketSignalCompletion
from evorove_lead.market_signals import MarketSignalStore, check_signals_for_business
from evorove_lead.presence import HttpPresenceSource
from evorove_lead.sqlalchemy_models import SearchTargetRow
from evorove_lead.web_people_search import WebSearchPeopleSearch
from evorove_lead.web_search import WebSearchClient, client_from_env

LOGGER = logging.getLogger(__name__)
RERUN_AFTER = timedelta(hours=20)  # a daily cron always finds yesterday's targets due


@dataclass(frozen=True)
class SearchTarget:
    business_id: str
    site_url: str
    business_archetype: str
    created_at: datetime
    last_run_at: datetime | None = None
    last_status: str = "queued"  # queued | running | people_found | no_fit | ... | failed
    last_cold: int = 0


class SearchTargetStore(Protocol):
    def save(self, target: SearchTarget) -> None: ...

    def get(self, business_id: str) -> SearchTarget | None: ...

    def due(self, now: datetime) -> Sequence[SearchTarget]: ...

    def all_targets(self) -> Sequence[SearchTarget]: ...


class InMemorySearchTargetStore:
    def __init__(self) -> None:
        self.targets: dict[str, SearchTarget] = {}
        self._lock = RLock()
        self._owners = {}
        self.details = {}

    def save(self, target: SearchTarget) -> None:
        self.targets[target.business_id] = target

    def get(self, business_id: str) -> SearchTarget | None:
        return self.targets.get(business_id)

    def due(self, now: datetime) -> Sequence[SearchTarget]:
        return tuple(t for t in self.targets.values() if _is_due(t, now))

    def all_targets(self) -> Sequence[SearchTarget]:
        return tuple(self.targets.values())


class SqlAlchemySearchTargetStore:
    def __init__(self, engine) -> None:
        self.database_engine = engine
        self._session_factory: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def save(self, target: SearchTarget) -> None:
        with self._session_factory() as session:
            session.merge(SearchTargetRow(**target.__dict__))
            session.commit()

    def get(self, business_id: str) -> SearchTarget | None:
        with self._session_factory() as session:
            row = session.get(SearchTargetRow, business_id)
            return _from_row(row) if row is not None else None

    def all_targets(self) -> Sequence[SearchTarget]:
        with self._session_factory() as session:
            rows = session.scalars(select(SearchTargetRow)).all()
            return tuple(_from_row(row) for row in rows)

    def due(self, now: datetime) -> Sequence[SearchTarget]:
        with self._session_factory() as session:
            rows = session.scalars(select(SearchTargetRow)).all()
            return tuple(t for t in (_from_row(row) for row in rows) if _is_due(t, now))


def _from_row(row: SearchTargetRow) -> SearchTarget:
    return SearchTarget(
        business_id=row.business_id,
        site_url=row.site_url,
        business_archetype=row.business_archetype or "",
        created_at=_aware(row.created_at),
        last_run_at=_aware(row.last_run_at) if row.last_run_at else None,
        last_status=row.last_status,
        last_cold=row.last_cold or 0,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


def _is_due(target: SearchTarget, now: datetime) -> bool:
    return target.last_status not in {"running", "interrupted"} and (target.last_run_at is None or now - target.last_run_at >= RERUN_AFTER)


def search_targets_from_env() -> SearchTargetStore:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return InMemorySearchTargetStore()
    return SqlAlchemySearchTargetStore(create_engine(database_url, future=True))


EngineFactory = Callable[[], "LeadGenerationEngine | None"]


def live_engine() -> LeadGenerationEngine | None:
    """Build live search; missing/invalid endpoint raises a sanitized SearchError."""

    client = client_from_env(required=True)
    if client is None:
        return None
    from evorove_lead.sqlalchemy_warehouse import warehouse_from_env
    from evorove_lead.research_config import research_pipeline_from_env
    warehouse = warehouse_from_env()
    research = research_pipeline_from_env(warehouse, client)
    return LeadGenerationEngine(
        warehouse=warehouse,
        research_pipeline=research,
        presence=HttpPresenceSource(),
        hypothesis_search=WebSearchPeopleSearch(
            client, decision_maker_llm=None if research else decision_maker_completion_from_env()
        ),
        infer_geo_radius_from_brief=True,
        marketing_analyzer=None if research else marketing_analyzer_from_env(),
    )


class _MemoryOwner(RunOwner):
    """Process-only equivalent for the explicitly nonpersistent stub store."""
    def __init__(self, store, target):
        from uuid import uuid4
        self.targets, self.target = store, target
        self.store = type("MemoryBackend", (), {"engine": None})()
        self.business_id, self.run_id = target.business_id, uuid4().hex
        self.done = False

    def check(self):
        with self.targets._lock:
            if self.targets._owners.get(self.business_id) is not self or self.done:
                raise OwnershipLost("run_ownership_lost")

    def heartbeat(self):
        self.check()

    def finish(self, status, reason="", cold=0):
        with self.targets._lock:
            self.check()
            self.targets.save(replace(self.target, last_status=status, last_cold=cold, last_run_at=datetime.now(timezone.utc)))
            self.targets.details[self.business_id] = {"stop_reason": reason, "attempt_id": self.run_id}
            self.targets._owners.pop(self.business_id)
            self.done = True


def claim_target(store, engine, target):
    db = getattr(engine._warehouse, "database_engine", None) if hasattr(engine, "_warehouse") else None
    db = db if db is not None else getattr(store, "database_engine", None)
    if db is not None:
        owner = LeaseStore(db).acquire(target.business_id, target=target,
                                      expected_last_run=target.last_run_at, compare_target=True)
        if isinstance(store, InMemorySearchTargetStore):
            store.save(replace(target, last_status="running"))
        return owner
    with store._lock:
        current = store.get(target.business_id)
        if target.business_id in store._owners or (current and current.last_status == "running"):
            raise RunBusy("run_already_owned")
        if current and current.last_run_at != target.last_run_at:
            raise RunBusy("target_already_ran")
        owner = _MemoryOwner(store, target)
        store._owners[target.business_id] = owner
        store.save(replace(target, last_status="running"))
        return owner


def run_target(store: SearchTargetStore, engine: LeadGenerationEngine, target: SearchTarget, *, owner=None, prepare=None) -> SearchTarget:
    """Claim once; completion is fenced by the same attempt as generation."""
    try:
        owner = owner or claim_target(store, engine, target)
    except RunBusy:
        return store.get(target.business_id) or target
    try:
        with owner.activate():
            extra = guarded_call(prepare) if prepare else ()
            if prepare and not extra:
                # A successful signal check with no news is not a failed search.
                owner.finish("no_fit")
            else:
                seed = BusinessSeed(site_url=target.site_url, business_id=target.business_id, business_archetype=target.business_archetype)
                result = guarded_call(engine.generate, seed, **({"extra_hypotheses": extra} if prepare else {}))
                owner.finish(result.status.value, result.stop_reason, len(result.handoffs))
    except OwnershipLost:
        pass  # The new owner/recovery owns the status; never write over it.
    except Exception as exc:
        LOGGER.error("search_run_failed business_id=%s error_type=%s", target.business_id, type(exc).__name__)
    finally:
        if isinstance(store, InMemorySearchTargetStore) and owner.store.engine is not None:
            persisted = SqlAlchemySearchTargetStore(owner.store.engine).get(target.business_id)
            if persisted:
                store.save(persisted)
    return store.get(target.business_id) or target


def target_details(store, engine, tenant):
    db = getattr(store, "database_engine", None)
    if db is None:
        db = getattr(getattr(engine, "_warehouse", None), "database_engine", None)
    if db is not None:
        from evorove_lead.intelligence_store import IntelligenceStore
        return IntelligenceStore(db).get(tenant, "search_status", "latest") or {}
    return getattr(store, "details", {}).get(tenant, {})


def recover_targets(store):
    db = getattr(store, "database_engine", None)
    if db is None:
        return
    leases = LeaseStore(db)
    for target in store.all_targets():
        if target.last_status == "running":
            leases.recover(target.business_id)


def run_due(store: SearchTargetStore, engine: LeadGenerationEngine, now: datetime | None = None) -> dict[str, object]:
    recover_targets(store)
    ran = cold = 0
    errors = []
    for target in store.due(now or datetime.now(timezone.utc)):
        outcome = run_target(store, engine, target)
        if outcome.last_status == "running" or outcome.last_run_at == target.last_run_at:
            continue
        ran += 1
        cold += outcome.last_cold
        if outcome.last_status in {"failed", "partial", "budget_exhausted"}:
            details = {}
            db = getattr(getattr(engine, "_warehouse", None), "database_engine", None)
            if db is not None:
                from evorove_lead.intelligence_store import IntelligenceStore
                details = IntelligenceStore(db).get(target.business_id, "search_status", "latest") or {}
            errors.append({"business_id": target.business_id, "status": outcome.last_status,
                           "stop_reason": details.get("stop_reason", "search_run_failed")})
    summary = {"ran": ran, "cold": cold}
    if errors:
        summary["errors"] = errors
    return summary


def run_due_market_signals(
    targets: SearchTargetStore,
    signal_store: MarketSignalStore,
    web_search_client: WebSearchClient,
    llm: MarketSignalCompletion,
    engine: LeadGenerationEngine,
) -> dict[str, object]:
    """Track 4's own daily cron: check every remembered business for market news.

    Independent of `run_due`'s 20-hour rerun window -- every registered
    business is checked every time this runs, since `market_signals.py`'s
    own dedup (one row per business_id + source_url) already stops the
    same news item from being re-billed. A business with no relevant news
    today costs one cheap search and, if `AI_PROVIDER=anthropic`, one small
    LLM call per unseen headline -- never a full search run.

    A signal-triggered run also re-derives that business's normal offer/
    marketing analysis (the same cost `run_due` already pays on its own
    schedule) -- accepted here for simplicity; the identity-dedup in
    `_generate_via_hypotheses` means no duplicate candidate ever reaches
    Cold twice, so the only cost is a second LLM call on a day both crons
    happen to fire for the same business.
    """

    from evorove_lead.web_search import SearchError
    checked = triggered = cold = 0
    errors = []
    recover_targets(targets)
    if getattr(engine, "_research_pipeline", None) is not None:
        # Market research is now inside the same bounded pipeline. Do not call
        # the legacy unbudgeted headline reader before entering it.
        for target in targets.all_targets():
            checked += 1
            if target.last_status == "interrupted":
                continue
            outcome = run_target(targets, engine, target)
            if outcome.last_status == "running" or outcome.last_run_at == target.last_run_at:
                continue
            triggered += 1
            cold += outcome.last_cold
            if outcome.last_status in {"failed", "partial", "budget_exhausted"}:
                errors.append({"business_id": target.business_id, "status": outcome.last_status,
                               "stop_reason": target_details(targets, engine, target.business_id).get("stop_reason", "search_run_failed")})
        summary = {"checked": checked, "triggered": triggered, "cold": cold}
        if errors:
            summary["errors"] = errors
        return summary
    recover_targets(targets)
    for target in targets.all_targets():
        checked += 1
        if target.last_status == "interrupted":
            continue
        found = []
        def prepare():
            hypotheses = check_signals_for_business(
                target.business_id, target.business_archetype, GeoRadius(), web_search_client, signal_store, llm
            )
            found.extend(hypotheses)
            return hypotheses
        outcome = run_target(targets, engine, target, prepare=prepare)
        triggered += bool(found)
        cold += outcome.last_cold if found else 0
        if outcome.last_status in {"failed", "partial", "budget_exhausted"}:
            errors.append({"business_id": target.business_id, "status": outcome.last_status,
                           "stop_reason": target_details(targets, engine, target.business_id).get("stop_reason", "search_run_failed")})
    summary = {"checked": checked, "triggered": triggered, "cold": cold}
    if errors:
        summary["errors"] = errors
    return summary


def main() -> int:
    """Daily cron entry point: `python -m evorove_lead.searches`."""

    import json
    import sys

    from evorove_lead.web_search import SearchError
    try:
        engine = live_engine()
    except SearchError as exc:
        print(json.dumps({"status": "failed", "stop_reason": exc.code}), file=sys.stderr)
        return 1
    if engine is None:
        print(json.dumps({"error": "people search is not connected (WEB_SEARCH_BASE_URL)"}), file=sys.stderr)
        return 1
    summary = run_due(search_targets_from_env(), engine)
    print(json.dumps(summary))
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GenerationStatus",
    "InMemorySearchTargetStore",
    "SearchTarget",
    "SearchTargetStore",
    "SqlAlchemySearchTargetStore",
    "live_engine",
    "run_due",
    "run_target",
    "search_targets_from_env",
]
