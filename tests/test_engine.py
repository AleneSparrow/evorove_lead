from evorove_lead.business import BusinessSeed
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
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
