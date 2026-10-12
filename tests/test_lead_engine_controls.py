"""Budget, evidence class, trace screens, and synthetic evaluation.

Synthetic metrics in this file are engineering fixtures. They are not a
claim about live search precision.
"""

from datetime import datetime, timedelta, timezone
from urllib.error import URLError

import pytest
from sqlalchemy import create_engine

from evorove_lead.business import BusinessSeed
from evorove_lead.cost_control import (
    BudgetExhausted,
    MeteredCompletion,
    RunLedger,
    estimate_tokens,
    price_for,
    reset_global_budget,
)
from evorove_lead.crm_touch import (
    MAX_DELIVERY_ATTEMPTS,
    CrmDeliveryError,
    HttpCrmLeadTouchSink,
    RecordingCrmDeliveryStore,
    redeliver_pending,
)
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.evaluation import SYNTHETIC, EvalCase, evaluate
from evorove_lead.hypothesis import GeoRadius, Hypothesis, IntentTrigger
from evorove_lead.materials import DepositedMaterial
from evorove_lead.offer_reader import read_offer
from evorove_lead.qualification import confirm_inferred, qualify
from evorove_lead.ranking import rank_score
from evorove_lead.selection import (
    EXPLICIT_INTENT,
    ICP_MATCH,
    INSUFFICIENT_EVIDENCE,
    SOURCE_KIND_BUSINESS,
    SelectionProfile,
    select,
)
from evorove_lead.sqlalchemy_warehouse import SqlAlchemyAnalysisWarehouse, create_all
from evorove_lead.trace_screen import screen_snippet
from evorove_lead.warehouse import HypothesisRecord, RecordingAnalysisWarehouse
from evorove_lead.web_people_search import WebSearchPeopleSearch
from evorove_lead.web_search import HttpSearxngWebSearchClient, SearchHit

HEATING = DepositedMaterial(
    name="site.html",
    body="Heating repair for homeowners in New York.\nWe fix furnaces, boilers and heat pumps the same day.",
)


def _profile() -> SelectionProfile:
    return SelectionProfile.from_offer(read_offer((HEATING,)), (HEATING,))


def test_keyword_overlap_is_not_labeled_as_purchase_intent() -> None:
    """Legacy Cold still keeps a service-word sentence. It is not EXPLICIT_INTENT."""

    profile = _profile()
    keyword = select("The furnace in the hallway is a furnace.", profile, addressable=True)
    assert keyword.evidence_class == INSUFFICIENT_EVIDENCE
    assert keyword.evidence_class != EXPLICIT_INTENT

    audience_only = select("We are homeowners in New York.", profile, addressable=True)
    assert audience_only.accepted and audience_only.evidence_class == ICP_MATCH

    explicit = select(
        "Can anyone recommend someone to fix my furnace? It stopped working.",
        profile,
        addressable=True,
    )
    assert explicit.accepted and explicit.evidence_class == EXPLICIT_INTENT

    company = select(
        "Family restaurant in Austin, open for dinner.",
        SelectionProfile.from_offer(
            read_offer(
                (
                    DepositedMaterial(
                        name="site.html",
                        body="Online booking for restaurants in Austin.\nWe set up table reservations and waitlists.",
                    ),
                )
            ),
            (
                DepositedMaterial(
                    name="site.html",
                    body="Online booking for restaurants in Austin.\nWe set up table reservations and waitlists.",
                ),
            ),
        ),
        addressable=True,
        source_kind=SOURCE_KIND_BUSINESS,
    )
    assert company.accepted and company.evidence_class == ICP_MATCH


def test_explicit_intent_is_not_sent_to_the_cheap_model() -> None:
    profile = _profile()
    qual = qualify(
        "Can anyone recommend someone to fix my furnace? It stopped working.",
        profile,
        addressable=True,
        source_kind="person",
        source_url="https://forum.example/1",
    )

    class Completion:
        calls = 0

        def complete(self, *, system: str, user: str) -> str:
            self.calls += 1
            return '{"evidence_class":"IRRELEVANT","evidence_quote":""}'

    completion = Completion()
    assert confirm_inferred(qual.why, qual, completion).evidence_class == EXPLICIT_INTENT
    assert completion.calls == 0


def test_trace_screen_rejects_non_latin_foreign_geo_and_stale_dates() -> None:
    language, _geo, reason = screen_snippet("Нужен мастер по отоплению")
    assert language == "non_latin" and "language" in reason

    _language, geo, foreign = screen_snippet("Need a furnace repair in Ontario Canada", locality="Austin")
    assert geo == "outside-us" and "geography" in foreign

    old = datetime.now(timezone.utc) - timedelta(days=900)
    _language, _geo, stale = screen_snippet("Need a furnace repair", published_at=old)
    assert "recency" in stale

    _language, _geo, fresh = screen_snippet("Need a furnace repair in Austin", locality="Austin")
    assert fresh == ""


def test_search_client_caches_success_and_retries_transport_errors() -> None:
    calls = {"n": 0}

    class Response:
        def read(self, n=-1):
            return b'{"results":[{"url":"https://forum.example/1","content":"need a furnace"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise URLError("down")
        return Response()

    client = HttpSearxngWebSearchClient(
        "http://searx.internal:8080", opener=opener, max_attempts=3, sleeper=lambda _seconds: None
    )
    hits = client.search("need a furnace")
    assert hits[0].url == "https://forum.example/1"
    assert calls["n"] == 3
    assert client.search("need a furnace") == hits
    assert calls["n"] == 3
    assert client.cache_hits == 1


def test_connector_does_not_fetch_a_foreign_or_duplicate_url() -> None:
    fetched: list[str] = []

    class Client:
        def search(self, query):
            return (
                SearchHit(url="https://forum.example/1", snippet="Need furnace repair in Canada"),
                SearchHit(url="https://forum.example/2", snippet="Need a furnace repair in Austin"),
                SearchHit(url="https://forum.example/2", snippet="Need a furnace repair in Austin"),
            )

    def fetch(url: str) -> str:
        fetched.append(url)
        return "Need a furnace repair. Email dana@example.com"

    hypothesis = Hypothesis(
        audience_segment="homeowners",
        channel="web_search",
        query_template="need furnace repair near Austin",
        intent_trigger=IntentTrigger(kind="need_statement", description="asks for repair"),
        geo_radius=GeoRadius(locality="Austin"),
    )
    findings = WebSearchPeopleSearch(Client(), page_fetcher=fetch).find(hypothesis)
    assert findings[0].reject_reason == "outside the brief's geography"
    assert findings[0].hit is None
    assert findings[2].reject_reason == "duplicate url in this query"
    assert fetched == ["https://forum.example/2"]


class _Presence:
    def load(self, seed):
        return (HEATING,)


class _CountingSearch:
    def __init__(self) -> None:
        self.queries = 0
        self.connected = True

    def find(self, hypothesis):
        self.queries += 1
        return ()


def test_run_budget_stops_before_the_next_hypothesis() -> None:
    search = _CountingSearch()
    engine = LeadGenerationEngine(
        presence=_Presence(),
        hypothesis_search=search,
        warehouse=RecordingAnalysisWarehouse(),
        run_budget=RunLedger(max_llm_calls=5, max_search_requests=1),
    )
    result = engine.generate(BusinessSeed(site_url="https://heat.example/", business_id="tenant-a"))
    assert search.queries == 1
    assert result.stopped_reason == "budget_exhausted"
    assert result.status is GenerationStatus.NO_FIT
    assert result.cost is not None
    assert result.cost["search_requests"] == 1
    assert result.cost["search_vendor_usd"] is None
    assert result.sent_messages == ()


def test_paused_query_is_not_searched_again() -> None:
    warehouse = RecordingAnalysisWarehouse()
    now = datetime.now(timezone.utc)
    template = '"need Heating repair for homeowners in New York."'
    warehouse.save_hypothesis(
        HypothesisRecord(
            id="hypothesis_old",
            business_id="tenant-a",
            brief_id="brief_old",
            audience_segment="homeowners in New York",
            channel="web_search",
            query_template=template,
            intent_trigger="need_statement",
            status="paused",
            fit_score=0.0,
            evidence_score=0.0,
            reach_estimate=0,
            created_at=now,
            query_budget=0,
        )
    )
    search = _CountingSearch()
    # The brief's need_statement query is built from the service text. Give
    # the stored row that exact template after we know it by running once
    # with a budget of zero searches... easier: set the template to whatever
    # build_hypotheses emits by inspecting one dry run without this row.
    engine = LeadGenerationEngine(
        presence=_Presence(), hypothesis_search=search, warehouse=warehouse
    )
    engine.generate(BusinessSeed(site_url="https://heat.example/", business_id="tenant-a"))
    paused_templates = {
        row.query_template for row in warehouse.hypotheses if row.status == "paused"
    }
    searched_before = search.queries
    # Mark every template this run just stored as paused with budget 0,
    # then a second run must not call the connector.
    for row in list(warehouse.hypotheses):
        if row.status != "paused":
            warehouse.save_hypothesis(
                HypothesisRecord(
                    id=row.id + "-paused",
                    business_id=row.business_id,
                    brief_id=row.brief_id,
                    audience_segment=row.audience_segment,
                    channel=row.channel,
                    query_template=row.query_template,
                    intent_trigger=row.intent_trigger,
                    status="paused",
                    fit_score=row.fit_score,
                    evidence_score=row.evidence_score,
                    reach_estimate=row.reach_estimate,
                    created_at=row.created_at + timedelta(seconds=1),
                    query_budget=0,
                )
            )
    search.queries = 0
    second = engine.generate(BusinessSeed(site_url="https://heat.example/", business_id="tenant-a"))
    assert search.queries == 0
    assert second.status is GenerationStatus.NO_FIT
    assert paused_templates
    assert searched_before > 0


def test_unknown_model_price_is_not_zero_and_blocks_the_call() -> None:
    class Inner:
        calls = 0

        def complete(self, *, system: str, user: str) -> str:
            self.calls += 1
            return "{}"

    inner = Inner()
    ledger = RunLedger(max_llm_calls=5, max_usd=1.0)
    metered = MeteredCompletion(inner, ledger, stage="deep_reasoning", model="claude-test")
    with pytest.raises(BudgetExhausted, match="unknown price"):
        metered.complete(system="s", user="u")
    assert inner.calls == 0
    assert ledger.unknown_price is True
    assert ledger.cost_usd is None
    assert price_for("claude-test") is None


def test_measured_haiku_usage_has_a_nonzero_list_price() -> None:
    class Inner:
        last_usage = {"input_tokens": 100, "output_tokens": 10}

        def complete(self, *, system: str, user: str) -> str:
            return "ok"

    ledger = RunLedger()
    metered = MeteredCompletion(
        Inner(), ledger, stage="low_cost_classification", model="claude-3-5-haiku-20241022"
    )
    assert metered.complete(system="s", user="u") == "ok"
    # 100 * $0.80 + 10 * $4.00 per million tokens.
    assert ledger.cost_usd == pytest.approx(0.00012)
    assert ledger.tokens_estimated is False
    assert ledger.input_tokens == 100
    assert metered.complete(system="s", user="u") == "ok"
    assert ledger.llm_calls == 1
    assert ledger.cache_hits == 1
    assert estimate_tokens("abcd") == 1


def test_cold_claim_is_once_per_identity_in_sqlite(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}", future=True)
    create_all(engine)
    warehouse = SqlAlchemyAnalysisWarehouse(engine)
    assert warehouse.claim_cold_identity("tenant-a", "Dana@Example.com") is True
    assert warehouse.claim_cold_identity("tenant-a", "dana@example.com") is False
    assert warehouse.claim_cold_identity("tenant-b", "dana@example.com") is True


def test_redelivery_stops_after_the_attempt_cap() -> None:
    class AlwaysDown(HttpCrmLeadTouchSink):
        def __init__(self) -> None:
            self.calls = 0

        def publish(self, business_id: str, payload: dict) -> None:
            self.calls += 1
            raise CrmDeliveryError("still down")

    store = RecordingCrmDeliveryStore()
    http = AlwaysDown()
    now = datetime.now(timezone.utc)
    store.record_failure("tenant-a", "touch-1", {"touch_id": "touch-1"}, "down", now)
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        redeliver_pending(http, store, clock=lambda: now)
    calls_at_cap = http.calls
    redeliver_pending(http, store, clock=lambda: now)
    assert calls_at_cap == MAX_DELIVERY_ATTEMPTS - 1 or http.calls == calls_at_cap
    assert http.calls == calls_at_cap
    assert store.pending_count() == 1


def test_synthetic_evaluation_is_labeled_and_not_real_world_accuracy() -> None:
    report = evaluate(
        [
            EvalCase("1", True, True, evidence_quote="need a furnace", source_text="need a furnace now", expected_class="EXPLICIT_INTENT", predicted_class="EXPLICIT_INTENT"),
            EvalCase("2", False, False, expected_class="IRRELEVANT", predicted_class="IRRELEVANT"),
            EvalCase("3", False, True, evidence_quote="not in source", source_text="unrelated", expected_class="IRRELEVANT", predicted_class="EXPLICIT_INTENT"),
            EvalCase("4", True, False, expected_duplicate=True, predicted_duplicate=True),
        ],
        k=2,
        cost_usd=None,
    )
    assert report.synthetic is SYNTHETIC
    assert report.as_dict()["real_world_accuracy"] is False
    assert report.cost_usd is None
    assert report.precision == pytest.approx(0.5)
    assert rank_score(evidence_class=EXPLICIT_INTENT, fit=1, evidence=1) > rank_score(
        evidence_class=ICP_MATCH, fit=1, evidence=1
    )
