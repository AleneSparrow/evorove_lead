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
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol, Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from evorove_lead.business import BusinessSeed
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.presence import HttpPresenceSource
from evorove_lead.sqlalchemy_models import SearchTargetRow
from evorove_lead.web_people_search import WebSearchPeopleSearch
from evorove_lead.web_search import client_from_env

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


class InMemorySearchTargetStore:
    def __init__(self) -> None:
        self.targets: dict[str, SearchTarget] = {}

    def save(self, target: SearchTarget) -> None:
        self.targets[target.business_id] = target

    def get(self, business_id: str) -> SearchTarget | None:
        return self.targets.get(business_id)

    def due(self, now: datetime) -> Sequence[SearchTarget]:
        return tuple(t for t in self.targets.values() if _is_due(t, now))


class SqlAlchemySearchTargetStore:
    def __init__(self, engine) -> None:
        self._session_factory: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def save(self, target: SearchTarget) -> None:
        with self._session_factory() as session:
            session.merge(SearchTargetRow(**target.__dict__))
            session.commit()

    def get(self, business_id: str) -> SearchTarget | None:
        with self._session_factory() as session:
            row = session.get(SearchTargetRow, business_id)
            return _from_row(row) if row is not None else None

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
    return target.last_status != "running" and (target.last_run_at is None or now - target.last_run_at >= RERUN_AFTER)


def search_targets_from_env() -> SearchTargetStore:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return InMemorySearchTargetStore()
    return SqlAlchemySearchTargetStore(create_engine(database_url, future=True))


EngineFactory = Callable[[], "LeadGenerationEngine | None"]


def live_engine() -> LeadGenerationEngine | None:
    """The open-web engine, or None when WEB_SEARCH_BASE_URL (SearxNG) is not set."""

    client = client_from_env()
    if client is None:
        return None
    return LeadGenerationEngine(
        presence=HttpPresenceSource(),
        hypothesis_search=WebSearchPeopleSearch(client),
        infer_geo_radius_from_brief=True,
    )


def run_target(store: SearchTargetStore, engine: LeadGenerationEngine, target: SearchTarget) -> SearchTarget:
    """One run for one business. Never raises: the outcome is the stored status."""

    now = datetime.now(timezone.utc)
    store.save(replace(target, last_status="running"))
    try:
        result = engine.generate(
            BusinessSeed(site_url=target.site_url, business_id=target.business_id, business_archetype=target.business_archetype)
        )
        done = replace(target, last_run_at=now, last_status=result.status.value, last_cold=len(result.handoffs))
    except Exception:  # noqa: BLE001 -- recorded; the next cron run tries again
        LOGGER.exception("search_run_failed business_id=%s", target.business_id)
        done = replace(target, last_run_at=now, last_status="failed", last_cold=0)
    store.save(done)
    return done


def run_due(store: SearchTargetStore, engine: LeadGenerationEngine, now: datetime | None = None) -> dict[str, int]:
    ran = cold = 0
    for target in store.due(now or datetime.now(timezone.utc)):
        outcome = run_target(store, engine, target)
        ran += 1
        cold += outcome.last_cold
    return {"ran": ran, "cold": cold}


def main() -> int:
    """Daily cron entry point: `python -m evorove_lead.searches`."""

    import json
    import sys

    engine = live_engine()
    if engine is None:
        print(json.dumps({"error": "people search is not connected (WEB_SEARCH_BASE_URL)"}), file=sys.stderr)
        return 1
    print(json.dumps(run_due(search_targets_from_env(), engine)))
    return 0


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
