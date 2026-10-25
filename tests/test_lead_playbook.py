"""Evorove ICP is data: lead segments, queries, and rules. No model and no network."""
from __future__ import annotations
from datetime import timedelta
from sqlalchemy import create_engine
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
    assert menu[0]["queries"] == 24
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
    assert len(seen) == 24
    for query in seen:
        tokens = query.casefold().split()
        assert "site" not in tokens and '"' not in query and "?" not in query
        assert len(query) <= 120


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
        age_days=1, context_only=False).rejection == "aggregator"
    assert screen_lead(book, evidence=QUOTE + " Book a call.", domain="linkedin.com", quote=QUOTE,
        age_days=1, context_only=False).rejection == "platform"
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
