"""A marketing page still yields a service and a buyer. A slogan and an FAQ do not.

The evorove.com fixture is the public landing copy from the sister repo's
web app, saved here so the test does not fetch the live site. The bakery
fixture is a different page shape (one service heading, one city, one FAQ)
so the same rules are not a patch for one site.
"""

from __future__ import annotations

import json
from pathlib import Path

from evorove_lead.business import BusinessSeed
from evorove_lead.client_zero import CLIENT_ZERO_SITE
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.geo import infer_geo_radius
from evorove_lead.marketing_analysis import MarketingAnalyzer, ScriptedMarketingCompletion
from evorove_lead.offer_reader import read_offer
from evorove_lead.presence import page_material
from evorove_lead.warehouse import RecordingAnalysisWarehouse

FIXTURES = Path(__file__).parent / "fixtures"
EVOROVE_HTML = (FIXTURES / "evorove_landing.html").read_text(encoding="utf-8")
BAKERY_HTML = (FIXTURES / "local_bakery.html").read_text(encoding="utf-8")
BAKERY_SITE = "https://sunrise-bakery.example/"

SERVICE_SENTENCE = (
    "It studies your setup, finds people in the open web, "
    "puts them on Cold, then sells until the deal is done"
)
BUYER_QUOTE = "After you describe the business: what you sell, who it's for, how you reach them"
# A short passage that contains "is" but is not a tagline. It must stay
# usable as evidence; the copula check only drops a single short sentence.
IS_PASSAGE = "You describe the business: what you sell, who it is for"
OPEN_WEB_QUOTE = "finds people in the open web"
FAQ_QUESTION = "What does Done mean for an offline vs online business?"
SLOGAN = "COLD IN. DONE ON THE BOARD."
OWNER_FILE_SENTENCE = "Sales automation and lead generation for small local service businesses."


def _evorove_material():
    return page_material(CLIENT_ZERO_SITE, EVOROVE_HTML)


def _bakery_material():
    return page_material(BAKERY_SITE, BAKERY_HTML)


def _segment(label: str, quote: str, channel: str, source: str) -> dict:
    return {
        "label": label,
        "evidence_quote": quote,
        "source_name": source,
        "channel": channel,
    }


def test_marketing_page_service_is_a_description_not_the_slogan() -> None:
    material = _evorove_material()
    offer = read_offer((material,), require_audience=False)

    sold = offer.what_we_sell[0].text
    assert SERVICE_SENTENCE in sold
    assert "COLD IN" not in sold
    assert not sold.startswith("Subscribe")
    assert "Then the board fills itself" not in sold
    assert "?" not in sold
    assert offer.who_may_fit == ()


def test_marketing_page_does_not_invent_a_city_from_cold_or_crm() -> None:
    radius = infer_geo_radius((_evorove_material(),))
    assert radius.locality == ""
    assert radius.country == "US"


def test_slogan_and_faq_question_are_not_marketing_facts() -> None:
    material = _evorove_material()
    raw = json.dumps(
        {
            "product": {"quote": SLOGAN, "source_name": CLIENT_ZERO_SITE},
            "price": None,
            "place": None,
            "promotion": {"quote": "Subscribe. Then the board fills itself.", "source_name": CLIENT_ZERO_SITE},
            "segments": [
                _segment(
                    "people asking what Done means",
                    FAQ_QUESTION,
                    "forums",
                    CLIENT_ZERO_SITE,
                ),
                _segment(
                    "business owners who describe what they sell and who it is for",
                    IS_PASSAGE,
                    "business_listing",
                    CLIENT_ZERO_SITE,
                ),
                _segment(
                    "businesses that want people found in the open web",
                    OPEN_WEB_QUOTE,
                    "forums",
                    CLIENT_ZERO_SITE,
                ),
            ],
        }
    )

    analysis = MarketingAnalyzer(ScriptedMarketingCompletion(raw)).analyze((material,))

    assert analysis.product is None
    assert analysis.promotion is None
    assert [segment.label for segment in analysis.segments] == [
        "business owners who describe what they sell and who it is for",
        "businesses that want people found in the open web",
    ]
    assert analysis.segments[0].evidence_quote == IS_PASSAGE
    assert all("?" not in segment.evidence_quote for segment in analysis.segments)
    assert SLOGAN not in {segment.evidence_quote for segment in analysis.segments}


class _RecordingSearch:
    connected = True

    def __init__(self) -> None:
        self.segments: list[str] = []

    def find(self, hypothesis):
        self.segments.append(hypothesis.audience_segment)
        return ()


def test_client_zero_brief_does_not_need_owner_materials() -> None:
    """Same wiring as client 0: the page is the brief. service.txt is not loaded."""

    material = _evorove_material()
    assert material.name == CLIENT_ZERO_SITE
    assert OWNER_FILE_SENTENCE not in material.body

    raw = json.dumps(
        {
            "product": {"quote": SERVICE_SENTENCE, "source_name": CLIENT_ZERO_SITE},
            "price": None,
            "place": None,
            "promotion": None,
            "segments": [
                _segment(
                    "business owners who describe what they sell and who it is for",
                    BUYER_QUOTE,
                    "business_listing",
                    CLIENT_ZERO_SITE,
                ),
                _segment(
                    "businesses that want people found in the open web",
                    OPEN_WEB_QUOTE,
                    "forums",
                    CLIENT_ZERO_SITE,
                ),
            ],
        }
    )

    class _PageOnly:
        def load(self, seed):
            assert seed.site_url == CLIENT_ZERO_SITE
            return (material,)

    search = _RecordingSearch()
    warehouse = RecordingAnalysisWarehouse()
    result = LeadGenerationEngine(
        presence=_PageOnly(),
        hypothesis_search=search,
        warehouse=warehouse,
        marketing_analyzer=MarketingAnalyzer(ScriptedMarketingCompletion(raw)),
    ).generate(
        BusinessSeed(
            site_url=CLIENT_ZERO_SITE,
            business_id="client-0",
            business_archetype="software subscription",
        )
    )

    assert result.status is GenerationStatus.NO_FIT
    assert result.sent_messages == ()
    assert SERVICE_SENTENCE in result.offer.what_we_sell[0].text
    assert "COLD IN" not in result.offer.what_we_sell[0].text
    assert search.segments[:2] == [
        "business owners who describe what they sell and who it is for",
        "businesses that want people found in the open web",
    ]
    assert all("?" not in segment for segment in search.segments)
    assert "independent salons" not in search.segments
    stored = warehouse.briefs[0].marketing_analysis
    assert stored["product"]["quote"] == SERVICE_SENTENCE
    assert stored["segments"][0]["evidence_quote"] == BUYER_QUOTE


def test_a_local_business_page_still_names_the_service_and_the_city() -> None:
    material = _bakery_material()
    offer = read_offer((material,))

    assert offer.what_we_sell[0].text == "Weekend catering"
    assert "HOT TRAYS" not in offer.what_we_sell[0].text
    audience = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "busy parents" in audience
    assert "corporate lunch" not in audience
    assert all("?" not in claim.text for claim in offer.who_may_fit)
    assert infer_geo_radius((material,)).locality == "Austin, TX"

    local_question = "What is included for a corporate lunch?"
    raw = json.dumps(
        {
            "product": {"quote": "HOT TRAYS. COLD MORNINGS.", "source_name": BAKERY_SITE},
            "price": {"quote": "$49", "source_name": BAKERY_SITE},
            "place": {"quote": "Austin, TX", "source_name": BAKERY_SITE},
            "promotion": None,
            "segments": [
                _segment("office managers ordering a corporate lunch", local_question, "news", BAKERY_SITE),
                _segment("busy parents", "busy parents", "forums", BAKERY_SITE),
                _segment("hosts of local events", "local events", "business_listing", BAKERY_SITE),
            ],
        }
    )
    analysis = MarketingAnalyzer(ScriptedMarketingCompletion(raw)).analyze((material,))

    assert analysis.product is None
    assert analysis.price is not None and analysis.price.quote == "$49"
    assert analysis.place is not None and analysis.place.quote == "Austin, TX"
    assert [segment.label for segment in analysis.segments] == ["busy parents", "hosts of local events"]
    assert local_question not in {segment.evidence_quote for segment in analysis.segments}
