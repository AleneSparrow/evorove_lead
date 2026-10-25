"""Evorove ICP is data: lead segments, queries, and rules. No model and no network."""
from __future__ import annotations
from datetime import timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from evorove_lead.client_zero import resolve_source_plan
from evorove_lead.lead_playbooks import (
    annual_price_cents, load_playbooks, niche_menu, plan_from_playbook, playbook_by_id,
    screen_lead, signal_temperature, team_larger_than_one,
)
from evorove_lead.source_analysis import SourceAnalyzer, warehouse_temperature
from evorove_lead.source_catalog import CATALOG, enabled_sources
from evorove_lead.source_core_models import SourceCompany, SourceContact, SourcePerson, SourceRawPage, SourceSignal
from evorove_lead.source_planner import PlannedSource, SourcePlan, cache_plan, client_zero_analysis
from evorove_lead.sqlalchemy_models import Base
from tests.test_source_core import NOW

QUOTE = "We are hiring an SDR."


def _book():
    return playbook_by_id("evorove_icp")


def test_menu_lists_lead_playbooks_not_the_clients_industry():
    menu = niche_menu()
    assert [item["id"] for item in menu] == ["evorove_icp"]
    assert menu[0]["title"] == "Evorove ICP" and menu[0]["describes"] == "lead_segments"
    assert menu[0]["queries"] == 12
    assert menu[0]["sources_collected"] == ["searxng_web"]
    assert {item["id"] for item in menu[0]["gaps"]} == {"job_board", "public_complaint_forum", "trade_press"}
    try:
        playbook_by_id("dentists")
    except RuntimeError as exc:
        assert "unknown niche" in str(exc)
    else:
        raise AssertionError("unknown niche")
    books = load_playbooks()
    assert len(books) == 1 and books[0].client_note


def test_playbook_queries_are_short_and_do_not_say_site():
    book = _book()
    assert book.minimum_usd_per_year == 5000
    assert book.event_days == 30 and book.context_days == 90
    seen = []
    for sub in book.subsegments:
        assert sub["queries"]
        seen.extend(sub["queries"])
    assert len(seen) == 12
    forbidden = {"hiring", "jobs", "job", "careers", "career", "site"}
    for query in seen:
        tokens = {token.casefold().strip(".,") for token in query.split()}
        assert not tokens & forbidden and '"' not in query and "?" not in query
        assert len(query) <= 120
        assert "b2b" in query.casefold() or "case studies" in query.casefold() or "book a call" in query.casefold()


def test_playbook_replaces_the_model_and_keeps_disabled_sources_out():
    book = _book()
    analysis = client_zero_analysis("client-zero", "It is for US B2B businesses with high-value deals.")
    plan = plan_from_playbook(analysis, book)
    assert plan.model_used is False
    assert [item.source_id for item in plan.selected] == ["searxng_web"]
    assert plan.selected[0].queries == book.queries()
    enabled = {item.id for item in enabled_sources()}
    assert set(item.source_id for item in plan.selected) <= enabled
    excluded = {item["id"]: item["why"] for item in plan.excluded}
    assert set(excluded) == {item.id for item in CATALOG if item.id != "searxng_web"}
    assert "needs_research" in excluded["hn_hiring"]
    assert "/api/" in excluded["osm_overpass"] and "needs_research" in excluded["osm_overpass"]
    assert "Disallow: /" in excluded["reddit"]
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cached = SourcePlan(analysis.brief_hash(), (PlannedSource("hn_show", "cached model choice", (), {}),), (), True)
    cache_plan(engine, analysis.business_id, cached, "stub", clock=lambda: NOW)
    chosen = resolve_source_plan(engine, analysis, book)
    assert chosen.model_used is False and chosen.selected[0].source_id == "searxng_web"
    assert resolve_source_plan(engine, analysis, None).selected[0].source_id == "hn_show"


def test_temperature_comes_from_the_signal_and_its_age():
    book = _book()
    sales = next(item for item in book.signals if item["id"] == "sales_hire")
    marketing = next(item for item in book.signals if item["id"] == "marketing_change")
    assert signal_temperature(book, sales, age_days=5, context_only=False) == "hot"
    assert signal_temperature(book, sales, age_days=40, context_only=True) == "warm"
    assert signal_temperature(book, marketing, age_days=10, context_only=False) == "warm"
    assert signal_temperature(book, marketing, age_days=40, context_only=True) == "cold"
    assert warehouse_temperature("ask_hn_need", age_days=1, context_only=False) == "warm"
    assert warehouse_temperature("show_hn_launch", age_days=2, context_only=False) == "cold"
    assert warehouse_temperature("web_mention", age_days=1, context_only=False) == "cold"


def test_deal_size_and_exclusions_do_not_call_a_model():
    book = _book()
    assert annual_price_cents("Plans from $99/month.") == (99 * 12 * 100,)
    assert annual_price_cents("Engagements from $5,000.") == (5000 * 100,)
    assert annual_price_cents("Retainers from $417/month.") == (417 * 12 * 100,)
    assert team_larger_than_one("Our team includes a founder and a sales lead.")
    assert not team_larger_than_one("Our team is friendly.")
    page = " Northwind is a marketing agency based in the United States. Book a call."
    fit = screen_lead(book, evidence=QUOTE + page, domain="northwind.example", quote=QUOTE, age_days=1, context_only=False)
    assert fit.rejection == "" and fit.kind == "sales_hire" and fit.temperature == "hot"
    assert fit.segment == "Agencies" and fit.reason == QUOTE
    assert screen_lead(book, evidence=QUOTE + " from $99/month. Book a call. Based in the United States.",
        domain="northwind.example", quote=QUOTE, age_days=1, context_only=False).rejection == "low_ticket"
    assert screen_lead(book, evidence=QUOTE + " from $99/month and from $8,000. Book a call.",
        domain="northwind.example", quote=QUOTE, age_days=1, context_only=False).rejection == "low_ticket"
    assert screen_lead(book, evidence=QUOTE + " Our law firm is based in the United States. Book a call.",
        domain="northwind.example", quote=QUOTE, age_days=1, context_only=False).rejection == "regulated"
    assert screen_lead(book, evidence=QUOTE + " Shop now for homeowners. Book a call.",
        domain="northwind.example", quote=QUOTE, age_days=1, context_only=False).rejection == "b2c"
    assert screen_lead(book, evidence=QUOTE + " A developer platform for SaaS companies. Book a call.",
        domain="northwind.example", quote=QUOTE, age_days=1, context_only=False).rejection == "saas_to_saas"
    assert screen_lead(book, evidence=QUOTE + " Book a call.", domain="indeed.com", quote=QUOTE,
        age_days=1, context_only=False).rejection == "job_board"
    assert screen_lead(book, evidence=QUOTE + " Book a call.", domain="linkedin.com", quote=QUOTE,
        age_days=1, context_only=False).rejection == "social"
    assert screen_lead(book, evidence="We updated the homepage. Book a call.", domain="northwind.example",
        quote="We updated the homepage.", age_days=1, context_only=False).rejection == "no_playbook_signal"
    high = screen_lead(book, evidence=QUOTE + " Work starts at $6,000.", domain="northwind.example",
        quote=QUOTE, age_days=1, context_only=False)
    assert high.rejection == "" and high.segment == "Evorove ICP"


def _store(engine, *, page, quote=QUOTE, days=1, context_only=False, domain="northwind.example"):
    when = NOW - timedelta(days=days)
    with Session(engine) as db, db.begin():
        db.add(SourceCompany(business_id="client-zero", domain=domain, name="Northwind",
            site_url=f"https://{domain}/", collected_at=NOW, expires_at=NOW + timedelta(days=7)))
        db.add(SourcePerson(business_id="client-zero", domain=domain, name_key="jane doe", name="Jane Doe", role="founder"))
        db.add(SourceContact(business_id="client-zero", domain=domain, email=f"jane@{domain}", name_key="jane doe",
            mx_verified=True, evidence_url=f"https://{domain}/"))
        db.add(SourceRawPage(business_id="client-zero", domain=domain, url=f"https://{domain}/", text=page,
            fetched_at=NOW, expires_at=NOW + timedelta(days=7), sha256="a" * 64))
        db.add(SourceSignal(business_id="client-zero", source_url=f"https://{domain}/news", domain=domain,
            source="searxng_web", signal_type="web_mention", signal_date=when, quote=quote, source_text=quote,
            fetched_at=NOW, context_only=context_only, is_advertisement=False, source_id="searxng_web"))


def test_playbook_analysis_emits_temperature_without_a_model():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page="Northwind is a marketing agency based in the United States. Book a call. Our team includes a founder and a sales lead.")
    class Model:
        def complete_bounded(self, **kwargs):
            raise AssertionError("playbook analysis called a model")
    report, touches = SourceAnalyzer(engine, Model(), clock=lambda: NOW, playbook=_book()).run("client-zero")
    assert report["cold_ready"] == 1 and report["model_analyzed"] == 0 and report["rules_passed"] == 1
    assert report["cost_usd"] == 0
    payload = touches[0]["payload"]
    assert payload["signal_temperature"] == "hot" and payload["detected_signal"] == "sales_hire"
    assert payload["reason"] == QUOTE and payload["segment"] == "Agencies"
    assert payload["hypothesis_id"] == "evorove_icp" and payload["source_id"] == "searxng_web"


def test_playbook_analysis_rejects_low_ticket_regulated_and_stale():
    book = _book()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page="Plans from $99/month. Northwind is based in the United States. Book a call.")
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=book).run("client-zero")
    assert touches == () and report["rejections"] == {"low_ticket": 1}
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page="Our law firm is based in the United States. Book a call.")
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=book).run("client-zero")
    assert touches == () and report["rejections"] == {"regulated": 1}
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page="Northwind is a marketing agency based in the United States. Book a call.", days=31)
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=book).run("client-zero")
    assert touches == () and report["rejections"] == {"stale_signal": 1}


def test_blocklist_english_and_company_root():
    from evorove_lead.company_facts import company_root, confirms_us, looks_english
    from evorove_lead.domain_blocklist import block_reason
    assert block_reason("jobs.indeed.com") == "job_board"
    assert block_reason("en.wikipedia.org") == "encyclopedia"
    assert block_reason("careers.microsoft.com") == "large_corporation"
    assert block_reason("dictionary.cambridge.org") == "dictionary"
    assert block_reason("example.com") == ""
    assert looks_english("Book a call for B2B teams.")
    assert not looks_english("B2B là gì? Tất tần tật về mô hình")
    assert not looks_english("企业软件解决方案与案例研究介绍")
    assert not looks_english("Book a call for B2B teams.", language="vi")
    assert confirms_us("Our office is in Ohio. Call +1 (415) 555-0134.")
    assert confirms_us("The company is based in the United States.")
    assert not confirms_us("We serve customers worldwide.")
    assert company_root("https://www.example.com/blog/post") == ("example.com", "https://example.com/")


def test_company_page_urls_stay_on_the_site_and_within_four():
    from evorove_lead.source_collection import company_page_urls
    html = (
        '<a href="/blog/post">x</a><a href="/careers">c</a>'
        '<a href="https://other.example/jobs">n</a><a href="/about">a</a>'
        '<a href="/pricing">p</a><a href="/team">t</a>'
    )
    urls = company_page_urls("https://example.com/", html)
    assert urls == (
        "https://example.com/",
        "https://example.com/careers",
        "https://example.com/pricing",
        "https://example.com/about",
    )
    assert "https://other.example/jobs" not in urls
    assert len(company_page_urls("https://example.com/", "")) == 4


def test_search_drops_blocklist_before_any_fetch_and_keeps_the_root(monkeypatch):
    from evorove_lead.source_fallback import UniversalWebFallback
    from evorove_lead.web_search import SearchHit
    monkeypatch.setattr("evorove_lead.source_fallback.require_public_http_url", lambda url, host_ok=None: url)
    class Search:
        def search(self, query):
            return (
                SearchHit("https://www.indeed.com/jobs/sales", "Hiring a sales director in the United States", title="Jobs"),
                SearchHit("https://en.wikipedia.org/wiki/Sales", "Sales is a business function", title="Sales"),
                SearchHit("https://vi.example/b2b", "B2B là gì? Tất tần tật về mô hình kinh doanh", title="B2B"),
                SearchHit("https://www.northwind.example/blog/post", "A marketing agency for B2B teams. Book a call.", title="Northwind"),
                SearchHit("https://northwind.example/about", "The same company again with another english snippet.", title="Again"),
            )
    class HTTP:
        def get(self, url):
            raise AssertionError(url)
    adapter = UniversalWebFallback(HTTP(), Search(), ("marketing agency for B2B book a call",))
    found = adapter.fetch()
    assert len(found) == 1
    assert found[0].site_url == found[0].source_url == "https://northwind.example/"
    assert found[0].signal_type == "company_candidate" and found[0].context_only is True
    assert adapter.filter_report()["kept"] == 1
    assert adapter.filter_report()["dropped"]["job_board"] == 1
    assert adapter.filter_report()["dropped"]["encyclopedia"] == 1
    assert adapter.filter_report()["dropped"]["non_english"] == 1
    assert adapter.filter_report()["dropped"]["duplicate"] == 1


def test_company_page_signal_without_a_date_is_context():
    from evorove_lead.lead_playbooks import find_company_page_signal
    book = _book()
    sales = next(item for item in book.signals if item["id"] == "sales_hire")
    found = find_company_page_signal(book, (
        ("https://northwind.example/", "Northwind is a marketing agency. Book a call.", "", "en"),
        ("https://northwind.example/careers", "We are hiring an SDR. The role is open.", "", "en"),
    ))
    assert found["source_url"] == "https://northwind.example/careers"
    assert found["quote"] == "We are hiring an SDR."
    assert found["context_only"] is True and found["signal_type"] == "sales_hire"
    assert signal_temperature(book, sales, age_days=0, context_only=True) == "warm"
    dated = find_company_page_signal(book, (
        ("https://northwind.example/careers", "We are hiring an SDR.", "2026-10-20T00:00:00Z", "en"),
    ))
    assert dated["context_only"] is False
    assert signal_temperature(book, sales, age_days=5, context_only=False) == "hot"
    assert find_company_page_signal(book, (
        ("https://northwind.example/careers", "我们正在招聘销售总监，欢迎加入我们的团队。", "", "zh"),
    )) is None


def test_collector_copies_the_company_page_sentence(monkeypatch):
    from evorove_lead.source_adapters import RawSignal
    from tests.test_source_core import NOW, setup
    engine, collector, fake = setup(monkeypatch)
    def reader(pages):
        url, text, published, language = pages[0]
        quote = "Jane Doe is founder of Example SaaS."
        assert quote in text
        return {"quote": quote, "source_url": url, "source_text": text, "signal_type": "sales_hire",
            "published": published, "context_only": True}
    collector.page_signals = reader
    quote = "A marketing agency for B2B teams. Book a call."
    signal = RawSignal("example.com", "", "https://example.com/", "company_candidate", NOW,
        "https://example.com/", quote, "searxng_web", quote, context_only=True, source_id="searxng_web")
    assert collector.add("client-zero", signal) == "collected"
    assert len(fake.urls) <= 5
    with Session(engine) as db:
        row = db.scalar(select(SourceSignal))
        assert row.signal_type == "sales_hire" and row.context_only is True
        assert row.quote == "Jane Doe is founder of Example SaaS." and row.quote in row.source_text
        assert row.source_url == "https://example.com/"
