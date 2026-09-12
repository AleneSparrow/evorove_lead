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


def test_connector_rejects_when_no_email_on_page():
    hypothesis = _hypothesis('"need Weekend catering" near Austin')
    client = FakeClient(
        (SearchHit(url="https://forum.example/1", snippet="Need weekend catering ASAP"),)
    )
    connector = WebSearchPeopleSearch(
        client, page_fetcher=_fetcher({"https://forum.example/1": "no contact info here"})
    )

    findings = connector.find(hypothesis)

    assert findings[0].hit is None
    assert findings[0].reject_reason == "no email address found on the page"


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
