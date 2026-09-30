"""Track 4: daily market/industry news, filtered, turned into one extra hypothesis.

Separate subsystem, not part of the main brief -> hypothesis -> trace
pipeline (module 1/2/3) -- it only ever *feeds* that pipeline one already-
built, already-grounded `Hypothesis` via `LeadGenerationEngine.generate`'s
`extra_hypotheses` hook. Own storage (`market_signals` table) so the same
news URL is never re-evaluated for a business that already saw it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from evorove_lead.hypothesis import GeoRadius, Hypothesis, IntentTrigger
from evorove_lead.market_signal_llm import (
    MarketSignalCompletion,
    RelevantSignal,
    evaluate_signal,
)
from evorove_lead.sqlalchemy_models import MarketSignalRow
from evorove_lead.web_search import SearchHit, WebSearchClient


def market_news_query(business_archetype: str) -> str:
    """A broad industry/market news query -- not tied to any named company."""

    archetype = (business_archetype or "").strip() or "small business"
    return f"{archetype} industry news"


@dataclass(frozen=True)
class MarketSignalRecord:
    """One news item this business has already been checked against.

    Kept whether or not it turned out relevant, so the same URL is never
    re-evaluated (and re-billed) for this business.
    """

    business_id: str
    source_url: str
    snippet: str
    relevant: bool
    segment_label: str
    channel: str
    evidence_quote: str
    checked_at: datetime


class MarketSignalStore(Protocol):
    def seen(self, business_id: str, source_url: str) -> bool: ...

    def record(self, signal: MarketSignalRecord) -> None: ...


class InMemoryMarketSignalStore:
    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()
        self.records: list[MarketSignalRecord] = []

    def seen(self, business_id: str, source_url: str) -> bool:
        return (business_id, source_url) in self._seen

    def record(self, signal: MarketSignalRecord) -> None:
        self._seen.add((signal.business_id, signal.source_url))
        self.records.append(signal)


class SqlAlchemyMarketSignalStore:
    def __init__(self, engine) -> None:
        self._session_factory: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def seen(self, business_id: str, source_url: str) -> bool:
        with self._session_factory() as session:
            return session.get(MarketSignalRow, (business_id, source_url)) is not None

    def record(self, signal: MarketSignalRecord) -> None:
        with self._session_factory() as session:
            session.merge(
                MarketSignalRow(
                    business_id=signal.business_id,
                    source_url=signal.source_url,
                    snippet=signal.snippet,
                    relevant=signal.relevant,
                    segment_label=signal.segment_label,
                    channel=signal.channel,
                    evidence_quote=signal.evidence_quote,
                    checked_at=signal.checked_at,
                )
            )
            session.commit()


def market_signal_store_from_env() -> MarketSignalStore:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return InMemoryMarketSignalStore()
    return SqlAlchemyMarketSignalStore(create_engine(database_url, future=True))


def discover_unseen_signals(
    business_id: str, business_archetype: str, client: WebSearchClient, store: MarketSignalStore
) -> tuple[SearchHit, ...]:
    """Search once; keep the hits this business hasn't already been checked against."""

    query = market_news_query(business_archetype)
    return tuple(
        hit for hit in client.search(query) if not store.seen(business_id, hit.url)
    )


def signal_hypothesis(signal: RelevantSignal, geo_radius: GeoRadius) -> Hypothesis:
    """Build the one extra hypothesis a relevant signal earns.

    Mirrors marketing_analysis.hypotheses_from_analysis's query shape:
    `business_listing` channel searches for companies of that kind;
    anything else is a web-search bet on a person showing that signal.
    """

    geo_suffix = f" near {geo_radius.locality}" if geo_radius.locality else ""
    if signal.channel == "business_listing":
        query = f"{signal.segment_label}{geo_suffix or ' in the US'}".strip()
    else:
        query = f"{signal.segment_label}{geo_suffix}".strip()
    return Hypothesis(
        audience_segment=signal.segment_label,
        channel=signal.channel,
        query_template=query,
        intent_trigger=IntentTrigger(
            kind="market_signal",
            description=f"Market news suggests searching for: {signal.segment_label}",
        ),
        geo_radius=geo_radius,
        audience_source="ai_inferred",
        evidence_quote=signal.evidence_quote,
    )


def check_signals_for_business(
    business_id: str,
    business_archetype: str,
    geo_radius: GeoRadius,
    client: WebSearchClient,
    store: MarketSignalStore,
    llm: MarketSignalCompletion,
    *,
    now: datetime | None = None,
) -> tuple[Hypothesis, ...]:
    """Check every unseen news hit; record each; return hypotheses for the relevant ones.

    Every hit gets recorded (relevant or not) before this returns, so a
    crash partway through never means the same URL gets re-checked and
    re-billed on the next run -- `record` happens right after `evaluate_signal`
    for that hit, not batched at the end.
    """

    checked_at = now or datetime.now(timezone.utc)
    hypotheses: list[Hypothesis] = []
    for hit in discover_unseen_signals(business_id, business_archetype, client, store):
        accepted = evaluate_signal(hit.snippet, business_archetype, llm)
        store.record(
            MarketSignalRecord(
                business_id=business_id,
                source_url=hit.url,
                snippet=hit.snippet,
                relevant=accepted is not None,
                segment_label=accepted.segment_label if accepted else "",
                channel=accepted.channel if accepted else "",
                evidence_quote=accepted.evidence_quote if accepted else "",
                checked_at=checked_at,
            )
        )
        if accepted is not None:
            hypotheses.append(signal_hypothesis(accepted, geo_radius))
    return tuple(hypotheses)
