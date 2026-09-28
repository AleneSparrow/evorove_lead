"""Module 2: search a business_listing segment down to a company list."""

from evorove_lead.business_listing import CompanyCandidate, discover_companies
from evorove_lead.hypothesis import GeoRadius, Hypothesis, IntentTrigger
from evorove_lead.web_search import SearchHit


class FakeClient:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    def search(self, query: str):
        self.queries.append(query)
        return self.hits


def _business_hypothesis(query_template: str = "restaurants near Austin") -> Hypothesis:
    return Hypothesis(
        audience_segment="restaurants",
        channel="web_search",
        query_template=query_template,
        intent_trigger=IntentTrigger(kind="business_listing", description="restaurants"),
        geo_radius=GeoRadius(locality="Austin"),
    )


def _person_hypothesis() -> Hypothesis:
    return Hypothesis(
        audience_segment="busy parents",
        channel="web_search",
        query_template="need catering near Austin",
        intent_trigger=IntentTrigger(kind="need_statement", description="asks for it"),
        geo_radius=GeoRadius(),
    )


def test_discover_companies_keeps_company_sites_and_drops_directories():
    client = FakeClient(
        (
            SearchHit(url="https://www.tonys-pizza.com/", snippet="Tony's restaurant in Austin"),
            SearchHit(url="https://www.yelp.com/biz/tonys", snippet="Best restaurants in Austin"),
        )
    )

    companies = discover_companies(_business_hypothesis(), client)

    assert companies == (
        CompanyCandidate(
            url="https://www.tonys-pizza.com/",
            registrable_domain="tonys-pizza.com",
            snippet="Tony's restaurant in Austin",
            query_used="restaurants near Austin",
        ),
    )
    assert client.queries == ["restaurants near Austin"]


def test_discover_companies_dedupes_by_registrable_domain():
    client = FakeClient(
        (
            SearchHit(url="https://www.tonys-pizza.com/", snippet="homepage"),
            SearchHit(url="https://www.tonys-pizza.com/menu", snippet="menu page"),
        )
    )

    companies = discover_companies(_business_hypothesis(), client)

    assert len(companies) == 1
    assert companies[0].url == "https://www.tonys-pizza.com/"


def test_discover_companies_drops_non_public_urls():
    client = FakeClient((SearchHit(url="http://localhost/1", snippet="internal"),))

    assert discover_companies(_business_hypothesis(), client) == ()


def test_discover_companies_rejects_non_business_listing_hypothesis():
    try:
        discover_companies(_person_hypothesis(), FakeClient(()))
    except ValueError as exc:
        assert "business_listing" in str(exc)
    else:
        raise AssertionError("expected ValueError")
