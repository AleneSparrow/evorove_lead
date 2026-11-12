"""Recorded HN and ATS payloads. No live network."""
import json
from pathlib import Path

from evorove_lead.crm_touch import RecordingLeadTouchSink
from evorove_lead.engine import GenerationResult, GenerationStatus, LeadGenerationEngine
from evorove_lead.hiring_boards import (
    JsonClient, board_links, collect_hiring, is_us_or_us_remote, keep_posting,
    published_business_email,
)
from evorove_lead.offer import GroundedClaim, OfferUnderstanding
from evorove_lead.warehouse import RecordingAnalysisWarehouse

FIXTURES = Path(__file__).parent / "fixtures"
OFFER = OfferUnderstanding(
    (GroundedClaim("Evorove carries the sales conversation through closing.", "service.txt"),),
    (GroundedClaim("US B2B businesses", "service.txt"),), (), (),
)


def _pages(url: str) -> str:
    if url == "https://bright.example/contact":
        return "Write to the company at sales@bright.example."
    if url == "https://bright.example/about":
        return "Bright builds software."
    raise AssertionError(url)


def _opener(url: str) -> bytes:
    if "search_by_date" in url:
        name = "hn_who_is_hiring.json"
    elif "/items/" in url:
        name = "hn_thread_1001.json"
    elif "boards/northwind/" in url:
        name = "greenhouse_board.json"
    elif "postings/bright" in url:
        name = "lever_board.json"
    elif "job-board/cedar" in url:
        name = "ashby_board.json"
    else:
        raise AssertionError(url)
    return (FIXTURES / name).read_bytes()


def test_board_tokens_come_only_from_published_links():
    text = (
        "https://jobs.ashbyhq.com/Cedar/1 "
        "https://job-boards.greenhouse.io/northwind "
        "https://jobs.lever.co/bright "
        "https://linkedin.com/jobs/view/1"
    )
    assert board_links(text) == tuple(board_links(
        "https://jobs.ashbyhq.com/Cedar https://job-boards.greenhouse.io/northwind https://jobs.lever.co/bright"
    ))
    assert [board.kind for board in board_links(text)] == ["ashby", "greenhouse", "lever"]
    assert published_business_email("hello@northwind.example") == "hello@northwind.example"
    assert published_business_email("jane.doe@northwind.example") == ""
    assert published_business_email("hello@" + "gmail.com") == ""
    assert published_business_email("") == ""


def test_us_sales_roles_with_a_first_or_founding_mark_are_kept():
    assert keep_posting("Founding Account Executive", "United States", "The company has no sales team.")
    assert keep_posting("Account Executive", "Austin, TX", "Founder-led sales still closes every deal at the company.")
    assert keep_posting("First Account Executive", "Remote - United States", "The company has no sales process.")
    assert not keep_posting("Founding Engineer", "United States", "The company has no sales team.")
    assert not keep_posting("Account Executive", "Remote", "The company has no sales team.")
    assert not keep_posting("Account Executive", "London, United Kingdom", "The company has no sales team.")
    assert is_us_or_us_remote("Remote, US")
    assert not is_us_or_us_remote("Remote")


def test_collect_uses_fixtures_and_does_not_guess_an_address(monkeypatch):
    monkeypatch.setenv("EVOROVE_CLIENT_ZERO_BUSINESS_ID", "client-zero")
    client = JsonClient(_opener, pause=0.0, limit=10)
    report = collect_hiring(client, fetch_text=_pages)
    assert report.threads_read == 1
    assert report.boards_found == 3
    assert report.boards_skipped == 0
    assert report.postings_read == 6
    assert [lead.company for lead in report.leads] == ["northwind", "bright", "cedar"]
    assert [lead.email for lead in report.leads] == [
        "hello@northwind.example", "sales@bright.example", "",
    ]
    assert report.leads[0].reason == "We have no sales team."
    assert "query=sales" not in "".join(client._cache)
    assert report.leads[1].reason == "The company has no sales process."
    assert report.leads[2].reason == "Founder-led sales still closes every deal at the company."
    again = client.get(next(iter(client._cache)))
    assert again
    assert client.requests == 5

    from evorove_lead.client_zero import run_hiring_board_search
    warehouse = RecordingAnalysisWarehouse()
    sink = RecordingLeadTouchSink()
    summary = run_hiring_board_search(
        dry_run=True, source=client, offer=OFFER, sink=sink, warehouse=warehouse, fetch_text=_pages,
    )
    assert summary["hiring"]["candidates"] == 3
    assert summary["hiring"]["with_pain"] == 3
    assert summary["hiring"]["without_pain"] == 0
    assert summary["hiring"]["with_contact"] == 2
    assert summary["hiring"]["without_contact"] == 1
    assert summary["handoffs"] == 0
    assert sink.published == []
    stored = warehouse.candidates
    assert [row.decision for row in stored] == ["cold", "cold", "needs_contact"]
    assert stored[2].email == ""

    live = run_hiring_board_search(
        dry_run=False, source=client, offer=OFFER, sink=sink, warehouse=warehouse, fetch_text=_pages,
    )
    assert live["handoffs"] == 2
    assert len(sink.published) == 2
    assert summary["observed_pains"] == [
        'Their job post says: "We have no sales team."',
        'Their job post says: "The company has no sales process."',
        'Their job post says: "Founder-led sales still closes every deal at the company."',
    ]
    assert all(item[1]["payload"].get("observed_pain") for item in sink.published)


def test_last_six_threads_dedupe_boards_and_the_cap_skips_the_rest():
    stories = {"hits": [
        {"objectID": str(number), "title": "Ask HN: Who is hiring? (Month 2026)"}
        for number in range(1, 7)
    ] + [{"objectID": "99", "title": "Show HN: something else"}]}
    thread = {"children": [
        {"text": "https://boards.greenhouse.io/northwind"},
        {"text": "https://jobs.lever.co/bright and again https://boards.greenhouse.io/northwind"},
        {"text": "https://jobs.ashbyhq.com/cedar"},
    ]}

    def opener(url: str) -> bytes:
        if "search_by_date" in url:
            assert "hitsPerPage=30" in url
            assert "query=sales" not in url
            return json.dumps(stories).encode()
        if "/items/" in url:
            return json.dumps(thread).encode()
        if "northwind" in url:
            return (FIXTURES / "greenhouse_board.json").read_bytes()
        if "bright" in url:
            return (FIXTURES / "lever_board.json").read_bytes()
        if "cedar" in url:
            return (FIXTURES / "ashby_board.json").read_bytes()
        raise AssertionError(url)

    client = JsonClient(opener, pause=0.0, limit=8)
    report = collect_hiring(client, fetch_text=lambda url: "")
    assert report.threads_read == 6
    assert report.boards_found == 3
    assert report.boards_skipped == 2
    assert client.requests == 8


def test_story_pages_continue_until_the_monthly_threads_are_found():
    def payload(page: int, full: bool) -> bytes:
        hits = [{"objectID": f"{page}-{index}", "title": "Show HN: noise"} for index in range(29 if full else 1)]
        hits.append({"objectID": str(100 + page), "title": "Ask HN: Who is hiring? (Month 2026)"})
        return json.dumps({"hits": hits}).encode()

    def opener(url: str) -> bytes:
        if "search_by_date" in url and "page=0" in url:
            return payload(0, True)
        if "search_by_date" in url and "page=1" in url:
            return payload(1, False)
        if "/items/" in url:
            return b'{"children":[{"text":"https://boards.greenhouse.io/northwind"}]}'
        if "northwind" in url:
            return b'{"jobs":[]}'
        raise AssertionError(url)

    client = JsonClient(opener, pause=0.0, limit=20)
    report = collect_hiring(client, fetch_text=lambda url: "", threads=2)
    assert report.threads_read == 2
    assert report.boards_found == 1
    assert report.boards_skipped == 0


def test_unresponsive_searx_uses_the_hiring_source(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("CLIENT_ZERO_HIRING_BOARDS", raising=False)
    result = GenerationResult(
        GenerationStatus.FAILED, None, (), (), (), stop_reason="search_sources_unresponsive",
    )
    monkeypatch.setattr(LeadGenerationEngine, "generate", lambda self, seed: result)

    def hiring(*, dry_run):
        return {"status": "people_found", "source": "hiring_boards", "dry_run": dry_run}

    monkeypatch.setattr("evorove_lead.client_zero.run_hiring_board_search", hiring)
    from evorove_lead.client_zero import run_client_zero_search
    summary = run_client_zero_search(research_dry_run=False)
    assert summary["searxng"] == "search_sources_unresponsive"
    assert summary["source"] == "hiring_boards"
    assert summary["dry_run"] is False
