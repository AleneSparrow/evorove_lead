"""Roadmap steps 18-19: B2B search re-selection and company-own contacts."""

from evorove_lead.business import BusinessSeed
from evorove_lead.crm_touch import RecordingLeadTouchSink
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.hypothesis import GeoRadius, Hypothesis, IntentTrigger
from evorove_lead.materials import DepositedMaterial
from evorove_lead.offer_reader import read_offer
from evorove_lead.search import PeopleHit
from evorove_lead.selection import COMPETITOR, NOT_TIED, SOURCE_KIND_BUSINESS, SelectionProfile, select
from evorove_lead.warehouse import RecordingAnalysisWarehouse
from evorove_lead.web_people_search import WebSearchPeopleSearch, pick_contact_email
from evorove_lead.web_search import SearchHit

HEATING = DepositedMaterial(
    name="site.html",
    body="Heating repair for homeowners in New York.\nWe fix furnaces, boilers and heat pumps the same day.",
)
BOOKING = DepositedMaterial(
    name="site.html",
    body="Online booking for restaurants in Austin.\nWe set up table reservations and waitlists.",
)


def _profile(material: DepositedMaterial) -> SelectionProfile:
    return SelectionProfile.from_offer(read_offer((material,)), (material,))


def test_furnace_request_fits_a_heating_repair_site_but_competitor_and_neighbour_do_not() -> None:
    profile = _profile(HEATING)

    asks = select("Can anyone recommend someone to fix my furnace in Manhattan? It stopped working.", profile, addressable=True)
    assert asks.accepted and asks.fit == 1.0 and asks.evidence == 1.0

    competitor = select("We repair furnaces in Manhattan. Licensed and insured, call us for a free estimate.", profile, addressable=True)
    assert not competitor.accepted and competitor.why == COMPETITOR

    neighbour = select("Need a plumber for a leaking kitchen sink in Manhattan.", profile, addressable=True)
    assert not neighbour.accepted and neighbour.why == NOT_TIED

    unreachable = select("My furnace broke, need help in Manhattan", profile, addressable=False)
    assert not unreachable.accepted and unreachable.fit == 1.0


def test_business_of_the_served_kind_fits_and_a_competitor_does_not() -> None:
    profile = _profile(BOOKING)
    restaurant = select("Family Italian restaurant in Austin, open for dinner. Reservations by phone only.", profile,
                        addressable=True, source_kind=SOURCE_KIND_BUSINESS)
    assert restaurant.accepted
    vendor = select("We offer online booking software for restaurants.", profile, addressable=True,
                    source_kind=SOURCE_KIND_BUSINESS)
    assert not vendor.accepted and vendor.why == COMPETITOR
    unrelated = select("Downtown law firm serving Austin families.", profile, addressable=True,
                       source_kind=SOURCE_KIND_BUSINESS)
    assert not unrelated.accepted


def test_contact_is_the_company_own_address_never_the_hosting_page_address() -> None:
    forum = "Posted by Sam. Questions? admin@forum.example.com | info@forum.example.com"
    assert pick_contact_email("https://forum.example.com/t/1", forum, business=False) is None
    assert pick_contact_email("https://forum.example.com/t/1", forum + " sam.k@gmail.com", business=False) == "sam.k@gmail.com"

    site = "Tony's Pizza, Austin. Email us: hello@tonys-pizza.com. Site by webmaster@agency.example"
    assert pick_contact_email("https://www.tonys-pizza.com/contact", site, business=True) == "hello@tonys-pizza.com"
    assert pick_contact_email("https://www.tonys-pizza.com/", "Reach Tony at tony.pizza@gmail.com", business=True) is None
    assert pick_contact_email("https://www.yelp.com/biz/tonys", "info@yelp.com hello@tonys-pizza.com", business=True) is None


def _business_hypothesis() -> Hypothesis:
    return Hypothesis(
        audience_segment="restaurants",
        channel="web_search",
        query_template="restaurants near Austin",
        intent_trigger=IntentTrigger(kind="business_listing", description="restaurants"),
        geo_radius=GeoRadius(locality="Austin"),
    )


def test_b2b_connector_takes_company_sites_and_skips_directories() -> None:
    class Client:
        def search(self, query):
            return (
                SearchHit(url="https://www.tonys-pizza.com/", snippet="Tony's restaurant in Austin, reservations by phone"),
                SearchHit(url="https://www.yelp.com/biz/tonys", snippet="Best restaurants in Austin"),
            )

    pages = {
        "https://www.tonys-pizza.com/": "Tony's restaurant. Contact: hello@tonys-pizza.com",
        "https://www.yelp.com/biz/tonys": "info@yelp.com",
    }
    findings = WebSearchPeopleSearch(Client(), page_fetcher=pages.__getitem__).find(_business_hypothesis())
    assert findings[0].hit is not None and findings[0].hit.identity == "hello@tonys-pizza.com"
    assert findings[0].hit.source_kind == "business"
    assert findings[1].hit is None and findings[1].reject_reason == "not the company's own website"


class _Presence:
    def load(self, seed):
        return (HEATING,)


class _Search:
    connected = True

    def find(self, offer):
        return (
            PeopleHit(
                identity="dana@example.com",
                observed_fact="Asked who can fix a furnace that stopped working in Manhattan.",
                observed_source="https://forum.example/t/9",
                channel="email",
            ),
        )


def test_a_person_already_on_the_board_is_not_handed_over_again() -> None:
    warehouse, sink = RecordingAnalysisWarehouse(), RecordingLeadTouchSink()
    seed = BusinessSeed(site_url="https://heat.example/", business_id="tenant-a")
    engine = LeadGenerationEngine(presence=_Presence(), people_search=_Search(), warehouse=warehouse, lead_touch_sink=sink)
    assert engine.generate(seed).status is GenerationStatus.PEOPLE_FOUND
    second = engine.generate(seed)
    assert second.status is GenerationStatus.NO_FIT
    assert second.rejected[0].why == "duplicate contact already handed to the board"
    assert len(sink.published) == 1
    kept = [c for c in warehouse.candidates if c.decision == "cold"]
    assert kept[0].fit == 1.0 and kept[0].evidence == 1.0
