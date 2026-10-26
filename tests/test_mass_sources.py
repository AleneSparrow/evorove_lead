"""Tests for the portrait classifier, ticker parse, and the search schedule. No network."""
from evorove_lead.lead_playbooks import playbook_by_id
from evorove_lead.mass_sources import (
    MAX_DOWNLOAD_BYTES, dedupe_companies, fits_portrait, parse_company_tickers, read_capped,
)
from evorove_lead.search_scale import expected_inflow, load_search_scale, next_batch, query_catalog

TICKERS = """
{"0":{"cik_str":1045810,"ticker":"NVDA","title":"NVIDIA CORP"},
 "1":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."},
 "2":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."},
 "3":{"cik_str":9,"ticker":"ACME","title":"Northwind Staffing Inc."}}
"""


def test_ticker_rows_dedupe_and_the_portrait_uses_code_or_name():
    rows = parse_company_tickers(TICKERS)
    assert [row["name"] for row in rows] == ["NVIDIA CORP", "Apple Inc.", "Northwind Staffing Inc."]
    assert rows[2]["domain"] == "" and rows[2]["cik"] == "9"
    mixed = dedupe_companies((
        {"name": "Northwind", "domain": "www.northwind.example", "naics": ""},
        {"name": "Northwind Other", "domain": "northwind.example", "naics": ""},
        {"name": "Plain Staffing", "domain": "", "naics": ""},
        {"name": "Plain Staffing", "domain": "", "naics": ""},
    ))
    assert [row["name"] for row in mixed] == ["Northwind", "Plain Staffing"]
    assert mixed[0]["domain"] == "northwind.example"
    assert fits_portrait("NVIDIA CORP") is False
    assert fits_portrait("Northwind Staffing Inc.") is True
    assert fits_portrait("Clinic Software", "5415") is False
    assert fits_portrait("Held Firm", "541810") is True
    assert fits_portrait("Held Firm", "621111") is False
    body, size = read_capped([b"a" * 10, b"b" * 10], max_bytes=15)
    assert body is None and size == 20
    kept, kept_size = read_capped([b"abc"], max_bytes=MAX_DOWNLOAD_BYTES)
    assert kept == b"abc" and kept_size == 3


def test_search_schedule_does_not_repeat_a_day_and_states_the_free_google_budget():
    scale = load_search_scale()
    assert scale["google_cse_free_queries_per_day"] == 100
    assert scale["searxng_queries_per_day"] == 30
    book = playbook_by_id("evorove_icp")
    queries = query_catalog(book, scale)
    active_terms = [term for sub in book.subsegments if sub.get("status") != "needs_research" for term in sub["terms"]]
    assert len(queries) == len(active_terms) * len(scale["cities"])
    assert queries[0] == f"{active_terms[0]} {scale['cities'][0]}"
    assert all("hiring" not in query.casefold() for query in queries)
    first, state = next_batch({}, queries, day="2026-10-26", budget=scale["searxng_queries_per_day"])
    assert len(first) == 30 and state["next_index"] == 30 and state["day"] == "2026-10-26"
    again, same = next_batch(state, queries, day="2026-10-26", budget=30)
    assert again == () and same["next_index"] == 30
    nxt, later = next_batch(state, queries, day="2026-10-27", budget=100)
    assert len(nxt) == 100 and later["next_index"] == 130 % len(queries)
    assert expected_inflow(queries_per_day=30, domains_per_query=0, a_rate=0)["A"] == 0
