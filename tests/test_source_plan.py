"""Catalog, deterministic plans, model validation, and the universal fallback."""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from evorove_lead.llm_usage import CompletionReply
from evorove_lead.platforms import is_platform
from evorove_lead.presence import PresenceRejected
from evorove_lead.source_adapters import (
    PublicSourceHTTP, SecFormDAdapter, form_d_index_url, parse_form_d_filing, parse_form_d_index, previous_business_day,
)
from evorove_lead.source_catalog import CATALOG, enabled_sources
from evorove_lead.source_core_models import SourceCompany, SourceSignal
from evorove_lead.source_fallback import UniversalWebFallback
from evorove_lead.source_planner import (
    ClientAnalysis, PlanRejected, adapters_for_plan, client_zero_analysis, dentist_example, deterministic_source_plan,
    pet_store_example, plan_system_prompt, plan_user_prompt, plan_with_model, validate_model_plan,
)
from evorove_lead.sqlalchemy_models import Base
from evorove_lead.web_search import SearchHit
from tests.test_source_core import NOW, FakeHTTP, setup

ZERO = client_zero_analysis("client-zero", "Evorove finds likely buyers for US B2B businesses with high-value deals that want to replace or avoid a sales team.")


def test_catalog_records_permission_and_does_not_enable_a_forbidden_source():
    ids = [item.id for item in CATALOG]
    assert len(ids) == len(set(ids))
    enabled = {item.id for item in enabled_sources()}
    assert enabled == {"hn_show", "hn_ask", "hn_hiring", "sec_form_d", "searxng_web"}
    for item in CATALOG:
        assert item.terms_checked_at == "2026-10-25"
        assert item.provides
        if item.status == "disabled":
            assert item.disabled_reason and item.automated_collection is False
    assert is_platform("github.com") and is_platform("www.github.com") and is_platform("huggingface.co")
    reddit = next(item for item in CATALOG if item.id == "reddit")
    assert reddit.needs_login and reddit.needs_key and "Disallow: /" in reddit.disabled_reason


def test_plans_follow_the_client_and_leave_hacker_news_out():
    zero = deterministic_source_plan(ZERO)
    dentist = deterministic_source_plan(dentist_example())
    pets = deterministic_source_plan(pet_store_example())
    assert [item.source_id for item in zero.selected] == ["searxng_web"]
    assert [item.source_id for item in dentist.selected] == ["searxng_web"]
    assert [item.source_id for item in pets.selected] == ["searxng_web"]
    assert all(item.source_id != "hn_show" for item in zero.selected)
    assert "b2b" in zero.selected[0].queries[0].casefold()
    assert "saas" not in zero.selected[0].queries[0].casefold()
    assert "site" not in zero.selected[0].queries[0].casefold().split()
    assert "united states" in zero.selected[0].queries[0].casefold()
    assert "austin" in dentist.selected[0].queries[0].casefold()
    assert "dental" in dentist.selected[0].queries[0].casefold()
    assert "pet" in pets.selected[0].queries[0].casefold()
    assert "austin" not in " ".join(pets.selected[0].queries).casefold()
    osm = next(item for item in dentist.excluded if item["id"] == "osm_overpass")
    assert "would have been eligible" in osm["why"] and "/api/" in osm["why"]
    assert "would have been eligible" not in next(item for item in pets.excluded if item["id"] == "osm_overpass")["why"]
    assert "does not mention" in next(item for item in zero.excluded if item["id"] == "hn_show")["why"]
    covered = {item.source_id for item in zero.selected} | {item["id"] for item in zero.excluded}
    assert covered == {item.id for item in CATALOG}
    capital = ClientAnalysis(business_id="example-raise", brief="A small US software company preparing a capital raise and a Form D filing.",
        customer_categories=("software company",), geography="United States", services=("software",),
        icp_signals=("preparing a Form D filing",))
    assert [item.source_id for item in deterministic_source_plan(capital).selected] == ["sec_form_d", "searxng_web"]
    assert adapters_for_plan(zero, object(), search=object())[0].source_id == "searxng_web"


def test_model_plan_is_limited_to_enabled_sources_and_cached():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    class Completion:
        calls = 0
        def complete_bounded(self, *, system, user, max_tokens, effort):
            self.calls += 1
            assert max_tokens == 800 and effort == "medium"
            assert "product_hunt" not in system and "osm_overpass" not in system
            assert "searxng_web" in user and "B2B" in user and "small SaaS" not in user
            return CompletionReply(json.dumps({"selected": [{"id": "searxng_web", "why": "The buyer's own site is on the public web.",
                "queries": ["US B2B businesses United States"], "filters": {"geography": "United States"}}],
                "excluded": [{"id": "hn_show", "why": "Side projects on Hacker News are not this buyer."}]}), 120, 60)
    completion = Completion()
    plan = plan_with_model(engine, ZERO, completion, clock=lambda: NOW)
    assert completion.calls == 1 and plan.model_used and plan.selected[0].queries == ("US B2B businesses United States",)
    assert plan_with_model(engine, ZERO, completion, clock=lambda: NOW).brief_hash == plan.brief_hash
    assert completion.calls == 1
    allowed = validate_model_plan(ZERO, {"selected": [{"id": "hn_show", "why": "This brief is about the Hacker News community.", "queries": []}]})
    assert allowed.selected[0].source_id == "hn_show"
    for bad in ("product_hunt", "linkedin", "osm_overpass"):
        try:
            validate_model_plan(ZERO, {"selected": [{"id": bad, "why": "no", "queries": ["x"]}]})
        except PlanRejected:
            continue
        raise AssertionError(bad)
    assert "enabled catalog" in plan_system_prompt()
    assert "hn_hiring" in plan_user_prompt(ZERO)


def test_form_d_keeps_only_a_filing_line_with_a_company_site():
    assert previous_business_day(date(2026, 10, 25)) == date(2026, 10, 23)
    assert "QTR4/form.20261023.idx" in form_d_index_url(date(2026, 10, 23))
    index = "\n".join((
        "10-K            Other Co                                                0000000002  20261023    edgar/data/2/other.txt",
        "D              Example Issuer LLC                                       0000000001  20261023    edgar/data/1/filing.txt",
    ))
    rows = parse_form_d_index(index)
    assert len(rows) == 1 and rows[0]["company"] == "Example Issuer LLC"
    body = "123 Secret Street\n<issuerWebsite>https://example.com</issuerWebsite>\n"
    signal = parse_form_d_filing(body, company=rows[0]["company"], filed=rows[0]["filed"], source_url="https://www.sec.gov/Archives/edgar/data/1/filing.txt")
    assert signal is not None and signal.site_url == "https://example.com" and signal.source_id == "sec_form_d"
    assert "Secret Street" not in signal.source_text and signal.quote in signal.source_text
    assert parse_form_d_filing("see https://www.sec.gov/Archives/edgar/data/1/filing.txt\n", company="Example", filed="20261023", source_url="https://www.sec.gov/Archives/x") is None
    assert parse_form_d_filing("code at https://github.com/acme/app\n", company="Example", filed="20261023", source_url="https://www.sec.gov/Archives/x") is None
    from tests.test_source_core import FakeResponse
    box = FakeHTTP('User-agent: *\nAllow: /Archives/edgar/data\n')
    def open_sec(request, timeout):
        url = request.full_url
        box.urls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(url, "User-agent: *\nAllow: /Archives/edgar/data\n")
        if url.endswith(".idx"):
            return FakeResponse(url, index + "\n")
        return FakeResponse(url, body)
    http = PublicSourceHTTP(opener=open_sec, clock=lambda: 0, sleep=lambda n: None, host_ok=lambda host: True, max_requests=10)
    found = SecFormDAdapter(http, clock=lambda: datetime(2026, 10, 25, tzinfo=timezone.utc)).fetch()
    assert len(found) == 1 and found[0].site_url == "https://example.com"
    assert any(url.endswith("form.20261023.idx") for url in box.urls)


def test_universal_fallback_skips_platforms_and_private_urls(monkeypatch):
    def allow(url, host_ok=None):
        if "127.0.0.1" in url:
            raise PresenceRejected("private")
        return url
    monkeypatch.setattr("evorove_lead.source_fallback.require_public_http_url", allow)
    class Search:
        def search(self, query):
            return (SearchHit("http://127.0.0.1/secret", "hidden"), SearchHit("https://github.com/acme/app", "a repository"),
                SearchHit("https://huggingface.co/acme/model", "a model card"),
                SearchHit("https://example.com/", "Jane Doe is founder of Example SaaS.", published="2026-10-20T00:00:00Z"))
    engine, collector, fake = setup(monkeypatch)
    plan_queries = deterministic_source_plan(ZERO).selected[0].queries
    stats = collector.collect("client-zero", (UniversalWebFallback(collector.http, Search(), plan_queries),))
    assert stats.get("signal:searxng_web") == 1
    with Session(engine) as db:
        assert db.scalar(select(SourceSignal)).source_id == "searxng_web"
        assert [row.domain for row in db.scalars(select(SourceCompany))] == ["example.com"]
    assert not any("github.com" in url or "127.0.0.1" in url for url in fake.urls)


def test_platform_signal_is_rejected_before_it_becomes_a_company(monkeypatch):
    engine, collector, fake = setup(monkeypatch)
    quote = "Show HN: a repository"
    from evorove_lead.source_adapters import RawSignal
    signal = RawSignal("Repo", "", "https://github.com/acme/app", "show_hn_launch", NOW - timedelta(days=1),
        "https://news.ycombinator.com/item?id=9", quote, "hn_show", quote, source_id="hn_show")
    class Adapter:
        def fetch(self):
            return (signal,)
    assert collector.collect("client-zero", (Adapter(),))["invalid_signal"] == 1
    with Session(engine) as db:
        assert db.scalars(select(SourceCompany)).all() == []
    assert fake.urls == []
