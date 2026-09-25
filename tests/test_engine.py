from evorove_lead.business import BusinessSeed
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.observations import OwnerObservationPeopleSearch
from evorove_lead.presence import page_material
from evorove_lead.search import PeopleHit, UnconnectedPeopleSearch


BAKERY_HTML = """
<html>
  <head><title>Sunrise Bakery</title></head>
  <body>
    <h1>Weekend catering for local events</h1>
    <p>We cook for busy parents in town.</p>
  </body>
</html>
"""
SITE = "https://sunrise-bakery.example/"
SEED = BusinessSeed(site_url=SITE)


class FakePresence:
    def __init__(self, html: str = BAKERY_HTML, url: str = SITE) -> None:
        self.url = url
        self.html = html

    def load(self, seed: BusinessSeed):
        assert seed.site_url == self.url
        return (page_material(self.url, self.html),)


class FakePeopleSearch:
    connected = True

    def __init__(self, hits: tuple[PeopleHit, ...]) -> None:
        self.hits = hits
        self.calls = 0

    def find(self, offer):  # noqa: ANN001
        self.calls += 1
        assert offer.what_we_sell
        return self.hits


def _engine(search) -> LeadGenerationEngine:  # noqa: ANN001
    return LeadGenerationEngine(presence=FakePresence(), people_search=search)


def test_engine_keeps_person_with_reason_tied_to_offer() -> None:
    search = FakePeopleSearch(
        (
            PeopleHit(
                identity="Jordan Lee, jordan@example-bakery.com",
                observed_fact="Already advertises weekend catering to nearby families.",
                observed_source="https://directory.example/jordan",
                channel="email",
            ),
        )
    )

    result = _engine(search).generate(SEED)

    assert result.status is GenerationStatus.PEOPLE_FOUND
    assert search.calls == 1
    assert result.sent_messages == ()
    assert len(result.candidates) == 1
    assert result.candidates[0].identity.startswith("Jordan Lee")
    assert result.handoffs[0].channel == "email"
    assert result.handoffs[0].reason_source == "https://directory.example/jordan"


def test_engine_drops_contact_dump_without_offer_reason() -> None:
    search = FakePeopleSearch(
        (
            PeopleHit(
                identity="555-0100",
                observed_fact="Has a phone number in a purchased list.",
                observed_source="phone dump",
            ),
            PeopleHit(
                identity="Sam Rivera, sam@parents.example",
                observed_fact="Writes a newsletter for busy parents.",
                observed_source="https://directory.example/sam",
            ),
        )
    )

    result = _engine(search).generate(SEED)

    assert result.status is GenerationStatus.PEOPLE_FOUND
    assert [hit.identity for hit in result.rejected] == ["555-0100"]
    assert result.candidates[0].identity.startswith("Sam Rivera")
    assert result.sent_messages == ()


def test_unconnected_search_understands_offer_and_does_not_find() -> None:
    search = UnconnectedPeopleSearch()
    engine = LeadGenerationEngine(presence=FakePresence(), people_search=search)

    result = engine.generate(SEED)

    assert result.status is GenerationStatus.SEARCH_UNCONNECTED
    assert result.offer is not None
    assert result.offer.what_we_sell[0].text == "Weekend catering"
    assert result.candidates == ()
    assert result.sent_messages == ()


def test_blank_url_does_not_pretend_to_have_an_offer() -> None:
    result = LeadGenerationEngine(presence=FakePresence()).generate(
        BusinessSeed(site_url="")
    )

    assert result.status is GenerationStatus.NEED_PRESENCE
    assert result.offer is None
    assert result.candidates == ()


def test_page_without_audience_is_not_an_offer() -> None:
    html = "<html><body><h1>Hello</h1><p>We exist.</p></body></html>"
    search = FakePeopleSearch(())
    engine = LeadGenerationEngine(
        presence=FakePresence(html=html),
        people_search=search,
    )

    result = engine.generate(SEED)

    assert result.status is GenerationStatus.OFFER_INCOMPLETE
    assert search.calls == 0
    assert result.candidates == ()


def test_empty_connected_search_is_no_fit_not_a_contact_list() -> None:
    search = FakePeopleSearch(())
    result = _engine(search).generate(SEED)

    assert search.calls == 1
    assert result.status is GenerationStatus.NO_FIT
    assert result.candidates == ()
    assert result.offer is not None


def test_owner_observations_keep_reasoned_people_and_reject_dumps(
    tmp_path,
) -> None:
    (tmp_path / "people.jsonl").write_text(
        "\n".join(
            [
                (
                    '{"name":"Jordan Lee","email":"jordan@example-bakery.com",'
                    '"observed_fact":"Already advertises weekend catering to nearby families.",'
                    '"observed_source":"owner-copied public post, 2026-09-01",'
                    '"channel":"email"}'
                ),
                (
                    '{"name":"Pat Dump","phone":"+15550100999",'
                    '"observed_fact":"Has a phone number in a purchased list.",'
                    '"observed_source":"phone dump","channel":"sms"}'
                ),
                (
                    '{"phone":"+15550100111","observed_fact":"",'
                    '"observed_source":"phone dump","channel":"sms"}'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    search = OwnerObservationPeopleSearch(tmp_path)
    engine = LeadGenerationEngine(presence=FakePresence(), people_search=search)

    result = engine.generate(SEED)

    assert search.connected is True
    assert result.status is GenerationStatus.PEOPLE_FOUND
    assert result.sent_messages == ()
    assert len(result.candidates) == 1
    assert result.candidates[0].identity.startswith("Jordan Lee")
    assert result.handoffs[0].channel == "email"
    assert result.handoffs[0].reason_source == "owner-copied public post, 2026-09-01"
    assert [hit.identity for hit in result.rejected] == ["Pat Dump, +15550100999"]


def test_empty_owner_observations_are_no_fit_not_a_web_directory(
    tmp_path,
) -> None:
    search = OwnerObservationPeopleSearch(tmp_path)
    result = _engine(search).generate(SEED)

    assert search.connected is True
    assert result.status is GenerationStatus.NO_FIT
    assert result.candidates == ()
    assert result.sent_messages == ()
    assert result.offer is not None


def test_engine_reports_assembled_people_to_crm_sink() -> None:
    from evorove_lead.crm_touch import RecordingLeadTouchSink
    from evorove_lead.business import BusinessSeed

    search = FakePeopleSearch(
        (
            PeopleHit(
                identity="Jordan Lee, jordan@example-bakery.com",
                observed_fact="Already advertises weekend catering to nearby families.",
                observed_source="https://directory.example/jordan",
                channel="email",
            ),
        )
    )
    sink = RecordingLeadTouchSink()
    seed = BusinessSeed(site_url=SITE, business_id="tenant-a")
    result = LeadGenerationEngine(
        presence=FakePresence(), people_search=search, lead_touch_sink=sink
    ).generate(seed)

    assert result.status is GenerationStatus.PEOPLE_FOUND
    assert len(sink.published) == 1
    business_id, payload = sink.published[0]
    assert business_id == "tenant-a"
    assert payload["kind"] == "assembled"
    assert payload["cycle"] == 1
    assert str(payload["person_id"]).startswith("ppl_")
    assert result.handoffs[0].person_id == payload["person_id"]


def test_sink_from_env_needs_url_and_secret(monkeypatch) -> None:
    from evorove_lead.crm_touch import (
        HttpCrmLeadTouchSink,
        NullLeadTouchSink,
        OutboxCrmLeadTouchSink,
        sink_from_env,
    )

    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    monkeypatch.delenv("INTERNAL_TASK_SECRET", raising=False)
    assert isinstance(sink_from_env(), NullLeadTouchSink)
    monkeypatch.setenv("CRM_BASE_URL", "http://crm.example")
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")
    sink = sink_from_env()
    assert isinstance(sink, OutboxCrmLeadTouchSink)
    assert isinstance(sink._http, HttpCrmLeadTouchSink)


def test_engine_writes_traces_and_rejections_to_its_own_warehouse_not_crm() -> None:
    """Phase 0 done-when: engine.py writes raw traces/rejections without going to CRM."""

    from evorove_lead.crm_touch import RecordingLeadTouchSink
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    search = FakePeopleSearch(
        (
            PeopleHit(
                identity="Jordan Lee, jordan@example-bakery.com",
                observed_fact="Already advertises weekend catering to nearby families.",
                observed_source="https://directory.example/jordan",
                channel="email",
            ),
            PeopleHit(
                identity="555-0100",
                observed_fact="Has a phone number in a purchased list.",
                observed_source="phone dump",
            ),
        )
    )
    warehouse = RecordingAnalysisWarehouse()
    crm_sink = RecordingLeadTouchSink()
    seed = BusinessSeed(site_url=SITE, business_id="tenant-a")
    result = LeadGenerationEngine(
        presence=FakePresence(),
        people_search=search,
        warehouse=warehouse,
        lead_touch_sink=crm_sink,
    ).generate(seed)

    assert result.status is GenerationStatus.PEOPLE_FOUND
    assert len(warehouse.briefs) == 1
    assert warehouse.briefs[0].business_id == "tenant-a"
    assert len(warehouse.hypotheses) == 1
    hypothesis_id = warehouse.hypotheses[0].id
    assert len(warehouse.traces) == 2
    assert {t.hypothesis_id for t in warehouse.traces} == {hypothesis_id}
    decisions = {c.decision for c in warehouse.candidates}
    assert decisions == {"cold", "rejected"}
    # CRM only ever hears about the accepted person, never the rejected one
    # or the raw trace text -- that stays in this repo's own warehouse.
    assert len(crm_sink.published) == 1


def test_engine_without_business_id_writes_nothing_to_warehouse() -> None:
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    search = FakePeopleSearch(
        (
            PeopleHit(
                identity="Jordan Lee, jordan@example-bakery.com",
                observed_fact="Already advertises weekend catering to nearby families.",
                observed_source="https://directory.example/jordan",
                channel="email",
            ),
        )
    )
    warehouse = RecordingAnalysisWarehouse()

    result = LeadGenerationEngine(
        presence=FakePresence(), people_search=search, warehouse=warehouse
    ).generate(SEED)

    assert result.status is GenerationStatus.PEOPLE_FOUND
    assert warehouse.briefs == []
    assert warehouse.traces == []


def test_engine_runs_the_full_hypothesis_pipeline_to_cold() -> None:
    """Phase 2 done-when: hypothesis -> query -> trace -> re-analysis -> Cold."""

    from evorove_lead.crm_touch import RecordingLeadTouchSink
    from evorove_lead.search import TraceFinding
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    class FakeHypothesisSearch:
        connected = True

        def __init__(self) -> None:
            self.queries: list[str] = []

        def find(self, hypothesis):
            self.queries.append(hypothesis.query_template)
            if hypothesis.intent_trigger.kind != "need_statement":
                return (
                    TraceFinding(
                        url="https://forum.example/off-topic",
                        raw_text="unrelated chatter",
                        query_used=hypothesis.query_template,
                        source_channel=hypothesis.channel,
                        reject_reason="not about the hypothesis's audience or service",
                    ),
                )
            return (
                TraceFinding(
                    url="https://forum.example/thread/1",
                    raw_text="Need weekend catering for a birthday",
                    query_used=hypothesis.query_template,
                    source_channel=hypothesis.channel,
                    hit=PeopleHit(
                        identity="jordan@example-bakery.com",
                        observed_fact="Publicly asked for weekend catering for a birthday.",
                        observed_source="https://forum.example/thread/1",
                        channel="email",
                    ),
                ),
                TraceFinding(
                    url="https://forum.example/no-contact",
                    raw_text="Need weekend catering too but no way to reach them",
                    query_used=hypothesis.query_template,
                    source_channel=hypothesis.channel,
                    reject_reason="no email address found on the page",
                ),
            )

    search = FakeHypothesisSearch()
    warehouse = RecordingAnalysisWarehouse()
    crm_sink = RecordingLeadTouchSink()
    seed = BusinessSeed(site_url=SITE, business_id="tenant-a")

    result = LeadGenerationEngine(
        presence=FakePresence(),
        hypothesis_search=search,
        warehouse=warehouse,
        lead_touch_sink=crm_sink,
    ).generate(seed)

    assert result.status is GenerationStatus.PEOPLE_FOUND
    assert len(result.candidates) == 1
    assert result.candidates[0].identity == "jordan@example-bakery.com"
    assert result.handoffs[0].reason_source == "https://forum.example/thread/1"

    # One brief snapshot, one hypothesis row per built hypothesis (2
    # demographic audiences + 2 B2B business listings + public_ask +
    # need_statement = 6), every raw trace recorded regardless of fate.
    assert len(warehouse.briefs) == 1
    assert len(warehouse.hypotheses) == 6
    assert len(warehouse.traces) == 7  # one per other hypothesis (5) + two from need_statement
    reject_reasons = {t.reason for t in warehouse.rejected_traces}
    assert "no email address found on the page" in reject_reasons
    assert any("not about" in r for r in reject_reasons)
    decisions = {c.decision for c in warehouse.candidates}
    assert decisions == {"cold"}
    assert len(crm_sink.published) == 1


def test_engine_hypothesis_pipeline_puts_hypothesis_id_on_the_handoff_and_crm_touch() -> None:
    """Phase 3 prerequisite: hypothesis_id must ride the handoff to Cold, not just the warehouse."""

    from evorove_lead.crm_touch import RecordingLeadTouchSink
    from evorove_lead.search import TraceFinding
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    class SingleHitHypothesisSearch:
        connected = True

        def find(self, hypothesis):
            if hypothesis.intent_trigger.kind != "need_statement":
                return ()
            return (
                TraceFinding(
                    url="https://forum.example/thread/1",
                    raw_text="Need weekend catering for a birthday",
                    query_used=hypothesis.query_template,
                    source_channel=hypothesis.channel,
                    hit=PeopleHit(
                        identity="jordan@example-bakery.com",
                        observed_fact="Publicly asked for weekend catering for a birthday.",
                        observed_source="https://forum.example/thread/1",
                        channel="email",
                    ),
                ),
            )

    warehouse = RecordingAnalysisWarehouse()
    crm_sink = RecordingLeadTouchSink()
    result = LeadGenerationEngine(
        presence=FakePresence(),
        hypothesis_search=SingleHitHypothesisSearch(),
        warehouse=warehouse,
        lead_touch_sink=crm_sink,
    ).generate(BusinessSeed(site_url=SITE, business_id="tenant-a"))

    assert len(result.handoffs) == 1
    hypothesis_id = result.handoffs[0].hypothesis_id
    assert hypothesis_id
    assert hypothesis_id in {h.id for h in warehouse.hypotheses}

    _, payload = crm_sink.published[0]
    assert payload["payload"]["hypothesis_id"] == hypothesis_id


def test_engine_persists_business_archetype_on_the_brief_and_reorders_by_pattern() -> None:
    from evorove_lead.pattern_library import RecordingPatternLibrary
    from evorove_lead.search import TraceFinding
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    class NoHitHypothesisSearch:
        connected = True

        def __init__(self) -> None:
            self.queries_in_order: list[str] = []

        def find(self, hypothesis):
            self.queries_in_order.append(hypothesis.intent_trigger.kind)
            return ()

    library = RecordingPatternLibrary()
    library.record_observation(
        business_archetype="bakery",
        channel_family="web_search",
        query_pattern="public_ask",
        close_rate=0.9,
        sample_size=10,
    )
    warehouse = RecordingAnalysisWarehouse()
    search = NoHitHypothesisSearch()
    seed = BusinessSeed(site_url=SITE, business_id="tenant-a", business_archetype="bakery")

    LeadGenerationEngine(
        presence=FakePresence(),
        hypothesis_search=search,
        warehouse=warehouse,
        pattern_library=library,
    ).generate(seed)

    assert warehouse.briefs[0].business_archetype == "bakery"
    # public_ask has a known "high" pattern for this archetype -> probed first.
    assert search.queries_in_order[0] == "public_ask"


def test_engine_infers_geo_radius_from_brief_only_when_opted_in() -> None:
    from evorove_lead.presence import page_material
    from evorove_lead.search import TraceFinding
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    html_with_city = """
    <html><body>
      <h1>Weekend catering for local events</h1>
      <p>Proudly serving Austin, TX since 2015. We cook for busy parents.</p>
    </body></html>
    """

    class RecordingHypothesisSearch:
        connected = True

        def __init__(self) -> None:
            self.geo_radii: list = []

        def find(self, hypothesis):
            self.geo_radii.append(hypothesis.geo_radius)
            return ()

    search_without_opt_in = RecordingHypothesisSearch()
    LeadGenerationEngine(
        presence=FakePresence(html=html_with_city),
        hypothesis_search=search_without_opt_in,
        warehouse=RecordingAnalysisWarehouse(),
    ).generate(SEED)
    assert all(g.locality == "" for g in search_without_opt_in.geo_radii)

    search_with_opt_in = RecordingHypothesisSearch()
    LeadGenerationEngine(
        presence=FakePresence(html=html_with_city),
        hypothesis_search=search_with_opt_in,
        warehouse=RecordingAnalysisWarehouse(),
        infer_geo_radius_from_brief=True,
    ).generate(SEED)
    assert all(g.locality == "Austin, TX" for g in search_with_opt_in.geo_radii)


def test_engine_explicit_geo_radius_always_wins_over_inference() -> None:
    from evorove_lead.hypothesis import GeoRadius
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    html_with_city = """
    <html><body>
      <h1>Weekend catering for local events</h1>
      <p>Proudly serving Austin, TX since 2015. We cook for busy parents.</p>
    </body></html>
    """

    class RecordingHypothesisSearch:
        connected = True

        def __init__(self) -> None:
            self.geo_radii: list = []

        def find(self, hypothesis):
            self.geo_radii.append(hypothesis.geo_radius)
            return ()

    search = RecordingHypothesisSearch()
    LeadGenerationEngine(
        presence=FakePresence(html=html_with_city),
        hypothesis_search=search,
        warehouse=RecordingAnalysisWarehouse(),
        geo_radius=GeoRadius(locality="Denver"),
        infer_geo_radius_from_brief=True,
    ).generate(SEED)

    assert all(g.locality == "Denver" for g in search.geo_radii)


def test_engine_hypothesis_pipeline_reports_unconnected_search() -> None:
    from evorove_lead.warehouse import RecordingAnalysisWarehouse

    class DisconnectedHypothesisSearch:
        connected = False

        def find(self, hypothesis):
            raise AssertionError("must not be called when disconnected")

    result = LeadGenerationEngine(
        presence=FakePresence(),
        hypothesis_search=DisconnectedHypothesisSearch(),
        warehouse=RecordingAnalysisWarehouse(),
    ).generate(BusinessSeed(site_url=SITE, business_id="tenant-a"))

    assert result.status is GenerationStatus.SEARCH_UNCONNECTED


def test_warehouse_from_env_needs_database_url(monkeypatch) -> None:
    from evorove_lead.sqlalchemy_warehouse import SqlAlchemyAnalysisWarehouse, warehouse_from_env
    from evorove_lead.warehouse import NullAnalysisWarehouse

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert isinstance(warehouse_from_env(), NullAnalysisWarehouse)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    assert isinstance(warehouse_from_env(), SqlAlchemyAnalysisWarehouse)
