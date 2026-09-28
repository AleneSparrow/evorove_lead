import json

from evorove_lead.decision_maker_llm import ScriptedDecisionMakerCompletion
from evorove_lead.hypothesis import GeoRadius, Hypothesis, IntentTrigger
from evorove_lead.presence import PresenceRejected
from evorove_lead.web_people_search import WebSearchPeopleSearch
from evorove_lead.web_search import SearchHit


class FakeClient:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    def search(self, query: str):
        self.queries.append(query)
        return self.hits


def _hypothesis(query_template: str) -> Hypothesis:
    return Hypothesis(
        audience_segment="busy parents",
        channel="web_search",
        query_template=query_template,
        intent_trigger=IntentTrigger(kind="need_statement", description="asks for it"),
        geo_radius=GeoRadius(),
    )


def _fetcher(pages: dict[str, str]):
    def fetch(url: str) -> str:
        if url not in pages:
            raise PresenceRejected("no such page")
        return pages[url]

    return fetch


def test_connector_extracts_email_and_keeps_the_hypothesis_query():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="https://forum.example/1", snippet="Need weekend catering ASAP"),)
    )
    connector = WebSearchPeopleSearch(
        client, page_fetcher=_fetcher({"https://forum.example/1": "reach me at jane@example.com"})
    )

    findings = connector.find(hypothesis)

    assert client.queries == ['"need Weekend catering" near Austin']
    assert len(findings) == 1
    finding = findings[0]
    assert finding.url == "https://forum.example/1"
    assert finding.query_used == hypothesis.query_template
    assert finding.hit is not None
    assert finding.hit.identity == "jane@example.com"
    assert finding.hit.channel == "email"
    assert finding.reject_reason == ""


def test_connector_rejects_off_topic_trace_without_fetching():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient((SearchHit(url="https://forum.example/1", snippet="my cat is cute"),))
    fetch_calls = []

    def fetch(url: str) -> str:
        fetch_calls.append(url)
        return "jane@example.com"

    connector = WebSearchPeopleSearch(client, page_fetcher=fetch)

    findings = connector.find(hypothesis)

    assert findings[0].hit is None
    assert findings[0].reject_reason
    assert fetch_calls == []


def test_connector_rejects_when_page_fetch_fails():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="https://forum.example/1", snippet="Need weekend catering ASAP"),)
    )
    connector = WebSearchPeopleSearch(client, page_fetcher=_fetcher({}))

    findings = connector.find(hypothesis)

    assert findings[0].hit is None
    assert findings[0].reject_reason == "no such page"


def test_connector_rejects_when_no_email_or_phone_on_page():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="https://forum.example/1", snippet="Need weekend catering ASAP"),)
    )
    connector = WebSearchPeopleSearch(
        client, page_fetcher=_fetcher({"https://forum.example/1": "no contact info here"})
    )

    findings = connector.find(hypothesis)

    assert findings[0].hit is None
    assert findings[0].reject_reason == "no email or phone found on the page"


def test_connector_falls_back_to_a_strictly_formatted_phone_number():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="https://forum.example/1", snippet="Need weekend catering ASAP"),)
    )
    connector = WebSearchPeopleSearch(
        client,
        page_fetcher=_fetcher({"https://forum.example/1": "call me at (415) 555-0134 anytime"}),
    )

    findings = connector.find(hypothesis)

    assert findings[0].hit is not None
    assert findings[0].hit.identity == "4155550134"
    assert findings[0].hit.channel == "phone"


def test_connector_ignores_zip_plus_four_and_prices_as_phone_numbers():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="https://forum.example/1", snippet="Need weekend catering ASAP"),)
    )
    connector = WebSearchPeopleSearch(
        client,
        page_fetcher=_fetcher(
            {"https://forum.example/1": "Ship to 94105-1234, total $1,234.5678 due"}
        ),
    )

    findings = connector.find(hypothesis)

    assert findings[0].hit is None
    assert findings[0].reject_reason == "no email or phone found on the page"


def test_connector_prefers_email_over_phone_when_both_present():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="https://forum.example/1", snippet="Need weekend catering ASAP"),)
    )
    connector = WebSearchPeopleSearch(
        client,
        page_fetcher=_fetcher(
            {"https://forum.example/1": "email jane@example.com or call 415-555-0134"}
        ),
    )

    findings = connector.find(hypothesis)

    assert findings[0].hit.channel == "email"
    assert findings[0].hit.identity == "jane@example.com"


def test_connector_rejects_non_public_result_url_without_fetching():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="http://localhost/1", snippet="Need weekend catering ASAP"),)
    )
    fetch_calls = []

    def fetch(url: str) -> str:
        fetch_calls.append(url)
        return "jane@example.com"

    connector = WebSearchPeopleSearch(client, page_fetcher=fetch)

    findings = connector.find(hypothesis)

    assert findings[0].hit is None
    assert fetch_calls == []


def test_connector_finds_nothing_when_client_returns_no_hits():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    connector = WebSearchPeopleSearch(FakeClient(()), page_fetcher=_fetcher({}))

    assert connector.find(hypothesis) == ()
    assert connector.connected is True


def _business_hypothesis(query_template: str = "restaurants near Austin") -> Hypothesis:
    return Hypothesis(
        audience_segment="restaurants",
        channel="web_search",
        query_template=query_template,
        intent_trigger=IntentTrigger(kind="business_listing", description="restaurants"),
        geo_radius=GeoRadius(locality="Austin"),
    )


def test_business_listing_traces_every_hit_including_a_dropped_directory():
    hypothesis = _business_hypothesis()
    client = FakeClient(
        (
            SearchHit(url="https://www.tonys-pizza.com/", snippet="Tony's restaurant in Austin"),
            SearchHit(url="https://www.yelp.com/biz/tonys", snippet="Best restaurants in Austin"),
        )
    )
    connector = WebSearchPeopleSearch(
        client,
        page_fetcher=_fetcher({"https://www.tonys-pizza.com/": "Tony's. Contact: hello@tonys-pizza.com"}),
    )

    findings = connector.find(hypothesis)

    assert len(findings) == 2
    assert findings[0].hit is not None
    assert findings[0].hit.identity == "hello@tonys-pizza.com"
    assert findings[0].hit.source_kind == "business"
    assert findings[1].hit is None
    assert findings[1].reject_reason == "not the company's own website"


def test_business_listing_traces_a_repeat_domain_without_fetching_it_twice():
    hypothesis = _business_hypothesis()
    client = FakeClient(
        (
            SearchHit(url="https://www.tonys-pizza.com/", snippet="Restaurants in Austin"),
            SearchHit(url="https://www.tonys-pizza.com/menu", snippet="Austin restaurants menu"),
        )
    )
    fetch_calls = []

    def fetch(url: str) -> str:
        fetch_calls.append(url)
        return "hello@tonys-pizza.com"

    connector = WebSearchPeopleSearch(client, page_fetcher=fetch)

    findings = connector.find(hypothesis)

    assert len(findings) == 2
    assert "https://www.tonys-pizza.com/menu" not in fetch_calls
    assert findings[1].hit is None
    assert findings[1].reject_reason == "same company already found in this search"


def test_business_listing_uses_the_llm_fallback_when_the_heuristic_finds_no_one():
    hypothesis = _business_hypothesis()
    client = FakeClient(
        (SearchHit(url="https://www.tonys-pizza.com/", snippet="Restaurants in Austin"),)
    )
    page_text = (
        "Tony's Pizza. Restaurants in Austin since 1994, run by Jane. "
        "Questions? jane@tonys-pizza.com"
    )
    connector = WebSearchPeopleSearch(
        client,
        page_fetcher=_fetcher({"https://www.tonys-pizza.com/": page_text}),
        decision_maker_llm=ScriptedDecisionMakerCompletion(
            json.dumps(
                {
                    "name": "Jane Doe",
                    "role": "Owner",
                    "email": "jane@tonys-pizza.com",
                    "evidence_quote": "Restaurants in Austin since 1994, run by Jane.",
                }
            )
        ),
    )

    findings = connector.find(hypothesis)

    assert len(findings) == 1
    assert findings[0].hit is not None
    assert findings[0].hit.identity == "jane@tonys-pizza.com"


def test_business_listing_falls_back_to_pick_contact_email_when_llm_also_finds_no_one():
    hypothesis = _business_hypothesis()
    client = FakeClient(
        (SearchHit(url="https://www.tonys-pizza.com/", snippet="Restaurants in Austin"),)
    )
    page_text = "Tony's Pizza. Restaurants in Austin since 1994. Questions? hello@tonys-pizza.com"
    connector = WebSearchPeopleSearch(
        client,
        page_fetcher=_fetcher({"https://www.tonys-pizza.com/": page_text}),
        decision_maker_llm=ScriptedDecisionMakerCompletion(
            json.dumps({"name": None, "role": None, "email": None, "evidence_quote": None})
        ),
    )

    findings = connector.find(hypothesis)

    assert len(findings) == 1
    assert findings[0].hit is not None
    assert findings[0].hit.identity == "hello@tonys-pizza.com"
