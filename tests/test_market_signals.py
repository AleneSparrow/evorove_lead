"""Track 4: news -> filter -> one extra hypothesis, with dedup."""

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine

from evorove_lead.hypothesis import GeoRadius
from evorove_lead.market_signal_llm import ScriptedMarketSignalCompletion
from evorove_lead.market_signals import (
    InMemoryMarketSignalStore,
    MarketSignalRecord,
    SqlAlchemyMarketSignalStore,
    check_signals_for_business,
    discover_unseen_signals,
    market_news_query,
    signal_hypothesis,
)
from evorove_lead.market_signal_llm import RelevantSignal
from evorove_lead.sqlalchemy_warehouse import create_all
from evorove_lead.web_search import SearchHit


class FakeClient:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    def search(self, query: str):
        self.queries.append(query)
        return self.hits


def _payload(**overrides):
    base = {
        "relevant": True,
        "segment_label": "salons hiring booth renters as employees",
        "channel": "industry_sites",
        "evidence_quote": "New licensing rules",
    }
    base.update(overrides)
    return json.dumps(base)


def test_market_news_query_is_broad_not_company_specific():
    assert market_news_query("salon software") == "salon software industry news"
    assert market_news_query("") == "small business industry news"


def test_discover_unseen_signals_uses_the_broad_query():
    client = FakeClient((SearchHit(url="https://a.example/1", snippet="New licensing rules"),))
    store = InMemoryMarketSignalStore()

    hits = discover_unseen_signals("biz-1", "salon software", client, store)

    assert client.queries == ["salon software industry news"]
    assert hits == client.hits


def test_discover_unseen_signals_skips_already_checked_urls():
    client = FakeClient((SearchHit(url="https://a.example/1", snippet="text"),))
    store = InMemoryMarketSignalStore()
    store._seen.add(("biz-1", "https://a.example/1"))

    assert discover_unseen_signals("biz-1", "salon software", client, store) == ()


def test_check_signals_for_business_records_every_hit_and_returns_hypotheses_for_relevant_ones():
    client = FakeClient(
        (
            SearchHit(url="https://a.example/1", snippet="New licensing rules for salons"),
            # Same scripted "relevant" reply, but its evidence_quote isn't in
            # THIS hit's own text -- grounding is per-hit, so this one must
            # still be rejected even though the model said "relevant".
            SearchHit(url="https://b.example/2", snippet="Unrelated sports news"),
        )
    )
    store = InMemoryMarketSignalStore()
    llm = ScriptedMarketSignalCompletion(_payload(evidence_quote="New licensing rules"))

    hypotheses = check_signals_for_business(
        "biz-1", "salon software", GeoRadius(locality="Austin"), client, store, llm
    )

    assert len(hypotheses) == 1
    assert len(store.records) == 2
    assert all(r.business_id == "biz-1" for r in store.records)
    assert store.seen("biz-1", "https://a.example/1")
    assert store.seen("biz-1", "https://b.example/2")
    by_url = {r.source_url: r for r in store.records}
    assert by_url["https://a.example/1"].relevant is True
    assert by_url["https://b.example/2"].relevant is False


def test_check_signals_for_business_records_an_irrelevant_hit_without_a_hypothesis():
    client = FakeClient((SearchHit(url="https://a.example/1", snippet="Unrelated"),))
    store = InMemoryMarketSignalStore()
    llm = ScriptedMarketSignalCompletion(
        json.dumps({"relevant": False, "segment_label": None, "channel": None, "evidence_quote": None})
    )

    hypotheses = check_signals_for_business(
        "biz-1", "salon software", GeoRadius(), client, store, llm
    )

    assert hypotheses == ()
    assert len(store.records) == 1
    assert store.records[0].relevant is False


def test_signal_hypothesis_business_listing_channel():
    signal = RelevantSignal(
        segment_label="independent salons hiring booth renters",
        channel="business_listing",
        evidence_quote="New licensing rules",
    )

    hyp = signal_hypothesis(signal, GeoRadius(locality="Austin"))

    assert hyp.intent_trigger.kind == "market_signal"
    assert hyp.audience_source == "ai_inferred"
    assert hyp.evidence_quote == "New licensing rules"
    assert hyp.query_template == "independent salons hiring booth renters near Austin"


def test_signal_hypothesis_web_search_channel_no_locality():
    signal = RelevantSignal(
        segment_label="salon owners asking about W-2 conversion",
        channel="forums",
        evidence_quote="New licensing rules",
    )

    hyp = signal_hypothesis(signal, GeoRadius())

    assert hyp.channel == "forums"
    assert hyp.query_template == "salon owners asking about W-2 conversion"


def test_sqlalchemy_store_round_trip(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'signals.db'}", future=True)
    create_all(engine)
    store = SqlAlchemyMarketSignalStore(engine)
    now = datetime.now(timezone.utc)

    assert store.seen("tenant-a", "https://a.example/1") is False

    store.record(
        MarketSignalRecord(
            business_id="tenant-a",
            source_url="https://a.example/1",
            snippet="New licensing rules",
            relevant=True,
            segment_label="salons hiring booth renters",
            channel="industry_sites",
            evidence_quote="New licensing rules",
            checked_at=now,
        )
    )

    assert store.seen("tenant-a", "https://a.example/1") is True
    assert store.seen("tenant-b", "https://a.example/1") is False
