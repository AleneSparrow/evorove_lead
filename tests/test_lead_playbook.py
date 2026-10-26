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
    assert menu[0]["queries"] == 6
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
    assert len(seen) == 12 and len(book.queries()) == 6
    paused = [sub["id"] for sub in book.subsegments if sub.get("status") == "needs_research"]
    assert paused == ["consulting_training", "b2b_services", "vertical_saas"]
    assert all("needs_research" in sub["why"] for sub in book.subsegments if sub.get("status") == "needs_research")
    assert all(query not in book.queries() for sub in book.subsegments if sub.get("status") == "needs_research" for query in sub["queries"])
    forbidden = {"hiring", "jobs", "job", "careers", "career", "site"}
    for query in seen:
        tokens = {token.casefold().strip(".,") for token in query.split()}
        assert not tokens & forbidden and '"' not in query and "?" not in query
        assert len(query) <= 120
        folded = query.casefold()
        assert "case studies" not in folded
        assert any(part in folded for part in ("book a call", "request a quote", "request a demo", "mid-size", "for b2b"))


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
    assert fit.segment == "Agencies" and fit.reason == "Northwind is a marketing agency based in the United States."
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
    missing_quote = screen_lead(book, evidence="We updated the homepage. Book a call.", domain="northwind.example",
        quote="We updated the homepage.", age_days=1, context_only=False)
    assert missing_quote.rejection == "" and missing_quote.level == "C" and "quote" in missing_quote.gaps
    portrait = screen_lead(book, evidence="Northwind is a marketing agency for B2B teams. Book a call.",
        domain="northwind.example", quote="We updated the homepage.", age_days=1, context_only=True)
    assert portrait.rejection == "" and portrait.temperature == "cold" and portrait.kind == "page_reason"
    high = screen_lead(book, evidence=QUOTE + " Work starts at $6,000.", domain="northwind.example",
        quote=QUOTE, age_days=1, context_only=False)
    assert high.rejection == "" and high.segment == "Evorove ICP"


def _store(engine, *, page, quote=QUOTE, days=1, context_only=False, domain="northwind.example", person=True, contact=True):
    when = NOW - timedelta(days=days)
    with Session(engine) as db, db.begin():
        db.add(SourceCompany(business_id="client-zero", domain=domain, name="Northwind",
            site_url=f"https://{domain}/", collected_at=NOW, expires_at=NOW + timedelta(days=7)))
        if person:
            db.add(SourcePerson(business_id="client-zero", domain=domain, name_key="jane doe", name="Jane Doe", role="founder"))
        if person and contact:
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
    assert payload["reason"] == "Northwind is a marketing agency based in the United States."
    assert payload["segment"] == "Agencies"
    assert payload["contact_channel"] == "email" and payload["contact_value"] == "jane@northwind.example"
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
    _store(engine, page="Northwind is a marketing agency based in the United States. Book a call.", days=100)
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
        "https://example.com/blog/post",
        "https://example.com/pricing",
    )
    assert "https://other.example/jobs" not in urls
    assert company_page_urls("https://example.com/", "") == ("https://example.com/",)


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


def test_saved_probe_snippets_separate_competitors_from_agencies():
    import json
    from pathlib import Path
    from evorove_lead.competitor_offers import competitor_offer, offer_surface
    rows = json.loads((Path(__file__).parent / "fixtures" / "step28_offer_snippets.json").read_text())["rows"]
    assert len(rows) == 10
    for row in rows:
        found = competitor_offer(f"{row['title']} {row['snippet']}")
        if row["expect"]:
            assert found == row["expect"], row["domain"]
        else:
            assert found == "", row["domain"]
    menu = "About Us Services ABM Analytics Brand Content " + ("word " * 30) + "Lead generation programs."
    assert competitor_offer(offer_surface(title="B2B Marketing Agency | Elevation", navigation=menu)) == ""
    assert competitor_offer(offer_surface(title="B2B Lead Generation Agency")) == "lead_generation"


def test_us_confirmation_needs_a_strong_sign_or_two_weak_ones():
    from evorove_lead.company_facts import assess_us
    assert assess_us("Our office is in Ohio.")["status"] == "confirmed"
    assert assess_us("Call +1 (415) 555-0134.")["status"] == "confirmed"
    assert assess_us("", domain="northwind.us")["status"] == "confirmed"
    assert assess_us("Hello.", schema_country="US")["status"] == "confirmed"
    assert assess_us("The company is based in the United States.")["status"] == "confirmed"
    weak = assess_us("Plans cost $8000 a year. Book a call.")
    assert weak["status"] == "needs_review" and weak["strong"] == ()
    assert assess_us("We are serving clients across the US. Plans cost $8000.")["status"] == "confirmed"
    assert assess_us("Book a call for B2B teams.", domain="northwind.com")["status"] == "absent"
    assert assess_us("Our office is in Ohio.", schema_country="GB")["status"] == "absent"


def test_crawl_order_prefers_a_company_title_over_an_aggregator(monkeypatch):
    from evorove_lead.source_fallback import UniversalWebFallback
    from evorove_lead.web_search import SearchHit
    monkeypatch.setattr("evorove_lead.source_fallback.require_public_http_url", lambda url, host_ok=None: url)
    class Search:
        def search(self, query):
            return (
                SearchHit("https://lists.example/best", "A roundup of firms.", title="10 Best marketing agency picks"),
                SearchHit("https://saleshive.com/", "Managed cold calling and email outreach.", title="B2B Sales Agency That Books Qualified Meetings"),
                SearchHit("https://notes.example/", "A short english note about work.", title="Studio notes from the team"),
                SearchHit("https://elevationb2b.com/", "Strategy, brand, and creative.", title="B2B Marketing Agency | Elevation"),
            )
    found = UniversalWebFallback(None, Search(), ("marketing agency for B2B book a call",), terms=("marketing agency",)).fetch()
    assert [item.site_url for item in found] == [
        "https://elevationb2b.com/",
        "https://notes.example/",
    ]
    report = UniversalWebFallback(None, Search(), ("marketing agency for B2B book a call",), terms=("marketing agency",))
    report.fetch()
    assert report.dropped["list_page"] == 1 and report.dropped["competitor"] == 1
    assert report.query_results == [{"query": "marketing agency for B2B book a call", "results": 4}]


def test_page_date_is_kept_and_a_missing_date_stays_context():
    from evorove_lead.lead_playbooks import find_company_page_signal, find_company_page_signals
    book = _book()
    found = find_company_page_signals(book, (
        ("https://northwind.example/blog", "We launched a new service on March 2, 2026.", "", "en"),
        ("https://northwind.example/careers", "We are hiring a marketing manager.", "", "en"),
    ))
    kinds = {item["signal_type"]: item for item in found}
    assert kinds["new_offer"]["published"] == "March 2, 2026" and kinds["new_offer"]["context_only"] is False
    assert kinds["open_marketing_role"]["published"] == "" and kinds["open_marketing_role"]["context_only"] is True
    long = "We're growing " + ("and hiring more people " * 20) + "."
    assert find_company_page_signal(book, (("https://northwind.example/careers", long, "", "en"),)) is None
    assert find_company_page_signal(book, (
        ("https://northwind.example/blog", "Imagine starting a new job with no guidance.", "", "en"),
    )) is None


def test_needs_review_does_not_become_cold():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page="We are hiring an SDR. Book a call. Plans cost $8000 a year.")
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=_book()).run("client-zero")
    assert touches == () and report["cold_ready"] == 0
    assert report["levels"]["C"] == 1 and "needs_review" not in report["rejections"]
    assert report["rejections"] == {}


def test_a_portrait_without_an_event_is_cold_and_a_missing_person_or_path_is_not():
    page = "Northwind is a marketing agency based in the United States. Book a call."
    ready = page + " Our team includes a founder and a sales lead."
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page=ready, quote="We updated the homepage.", context_only=True)
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=_book()).run("client-zero")
    assert report["cold_ready"] == 1 and report["levels"]["A"] == 1
    assert touches[0]["payload"]["signal_temperature"] == "cold"
    assert touches[0]["payload"]["temperature"] == "cold"
    assert touches[0]["payload"]["level"] == "A"
    assert touches[0]["payload"]["detected_signal"] == "page_reason"
    assert {"fit", "intent", "reach", "total", "gaps"} <= set(touches[0]["payload"])
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page=page, quote="We updated the homepage.", context_only=True)
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=_book()).run("client-zero")
    assert touches == () and report["levels"]["B"] == 1 and report["cold_ready"] == 0
    assert report["scored"][0]["temperature"] == "cold"
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page=page + " contact_form https://northwind.example/contact fields name|email|message",
        quote="We updated the homepage.", context_only=True, contact=False)
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=_book()).run("client-zero")
    assert touches == () and report["levels"]["B"] == 1
    assert report["scored"][0]["contact_channel"] == "form"
    assert report["scored"][0]["contact_value"] == "https://northwind.example/contact"
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page=page, person=False, contact=False)
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=_book()).run("client-zero")
    assert touches == () and report["cold_ready"] == 0 and "no_named_person" not in report["rejections"]
    assert report["levels"]["B"] == 1 and "named_decision_maker" in report["scored"][0]["gaps"]
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    _store(engine, page=page, contact=False)
    report, touches = SourceAnalyzer(engine, clock=lambda: NOW, playbook=_book()).run("client-zero")
    assert touches == () and "no_contact_path" not in report["rejections"]
    assert report["levels"]["B"] == 1
    assert "email_mx" in report["scored"][0]["gaps"] and "contact_form" in report["scored"][0]["gaps"]


def test_saved_list_page_yields_names_and_domains_without_the_description():
    from pathlib import Path
    from evorove_lead.list_pages import companies_from_list_html, is_list_page
    html = (Path(__file__).parent / "fixtures" / "top_agencies_list.html").read_text()
    assert is_list_page("10 Best marketing agencies", "A roundup")
    found = companies_from_list_html(html, "https://lists.example/best", limit=12)
    assert found == (
        ("Elevation B2B", "https://elevationb2b.com/work"),
        ("Northwind Studio", "https://northwind.example/"),
    )
    assert all("manufacturers" not in name for name, _url in found)
    assert len(companies_from_list_html(html, "https://lists.example/best", limit=1)) == 1


def test_leader_email_and_form_come_from_the_page_markup():
    from evorove_lead.decision_maker import find_named_leader
    from evorove_lead.page_extract import extract_html
    from evorove_lead.source_collection import classify_fetch_error, is_challenge_page
    from http.client import RemoteDisconnected
    from urllib.error import HTTPError
    html = """<html><head>
    <script type="application/ld+json">{"@type":"Person","name":"Maria Alvarez","jobTitle":"Founder","email":"maria@northwind.example"}</script>
    </head><body>
    <a href="mailto:hello@northwind.example">hello</a>
    <form action="/contact"><input name="email"><textarea name="message"></textarea></form>
    </body></html>"""
    page = extract_html("https://northwind.example/about", html)
    assert ("Maria Alvarez", "Founder") in page.people
    assert "maria@northwind.example" in page.emails and "hello@northwind.example" in page.emails
    assert page.forms[0][0] == "/contact" and page.forms[0][1] == ("email", "message")
    from evorove_lead.source_collection import contact_form_target
    assert contact_form_target(page.forms, "https://northwind.example/about")[0] == "https://northwind.example/contact"
    assert contact_form_target((("https://example.com/search", ("q",)),), "https://example.com/") == ("", "")
    found = find_named_leader((("https://northwind.example/about", page.text, page.people),))
    assert found[:2] == ("Maria Alvarez", "Founder")
    text = find_named_leader((("https://northwind.example/team", "Jane Doe is the managing director.", ()),))
    assert text[0] == "Jane Doe" and text[1].casefold() == "managing director"
    glued = find_named_leader((("https://northwind.example/about", "Case Study Jon Corrin is the CEO.", ()),))
    assert glued[0] == "Jon Corrin"
    assert find_named_leader((("https://northwind.example/about", "Professional Services Built is the owner.", ()),)) is None
    assert find_named_leader((("https://northwind.example/", "Imagine starting a new job with no guidance.", ()),)) is None
    from evorove_lead.lead_playbooks import fit_quotes
    assert fit_quotes(_book(), "3055 Contact Sales Enterprise Managed IT services for mid-size businesses nationwide.") == ()
    assert fit_quotes(_book(), "Northwind is a marketing agency for B2B teams.") == ("Northwind is a marketing agency for B2B teams.",)
    assert classify_fetch_error(HTTPError("https://example.com/", 403, "no", {}, None)) == "blocked_403"
    assert classify_fetch_error(RemoteDisconnected("Remote end closed connection without response")) == "blocked_connection"
    assert is_challenge_page("Client Challenge", "")
    assert not is_challenge_page("LarchGrid", "Managed IT for B2B companies in Ohio.")


def test_playbook_search_keeps_thirty_results_across_three_pages():
    import json
    from evorove_lead.web_search import HttpSearxngWebSearchClient, configure_playbook_search

    def opener(request, timeout=None):
        page = "1"
        if "pageno=2" in request.full_url:
            page = "2"
        elif "pageno=3" in request.full_url:
            page = "3"
        results = [{"url": f"https://firm{page}-{i}.example/", "content": "A marketing agency for B2B teams.", "title": "Agency"} for i in range(12)]
        body = json.dumps({"results": results}).encode()

        class Response:
            def read(self, n=-1):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()

    client = HttpSearxngWebSearchClient("http://127.0.0.1:8080", opener=opener, max_pages=3, max_requests=10, pause_seconds=0)
    hits = client.search("marketing agency for B2B book a call")
    assert len(hits) == 30 and client.requests == 3
    assert client.query_counts["marketing agency for B2B book a call"] == 30
    configure_playbook_search(client)
    assert client._max_pages == 3 and client.pause_seconds == 0.4
