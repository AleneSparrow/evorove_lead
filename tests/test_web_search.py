import json

import pytest

from evorove_lead.presence import PresenceRejected
from evorove_lead.web_search import (
    HttpSearxngWebSearchClient,
    SearchHit,
    client_from_env,
    fetch_page_text,
)


class FakeResponse:
    def __init__(self, body: bytes, url: str = "") -> None:
        self._body = body
        self._url = url

    def read(self, n=-1):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body: bytes, url: str = ""):
    def opener(request, timeout=None):
        return FakeResponse(body, url=url or request.full_url)

    return opener


def test_client_parses_searxng_shaped_results():
    body = json.dumps(
        {
            "results": [
                {"url": "https://forum.example/thread/1", "content": "looking for a barber"},
                {"url": "https://forum.example/thread/2", "content": "need catering downtown"},
            ]
        }
    ).encode("utf-8")
    client = HttpSearxngWebSearchClient("http://searx.internal:8080", opener=_opener(body))

    hits = client.search('"need catering"')

    assert hits == (
        SearchHit(url="https://forum.example/thread/1", snippet="looking for a barber"),
        SearchHit(url="https://forum.example/thread/2", snippet="need catering downtown"),
    )


def test_client_accepts_localhost_base_url():
    """A self-hosted SearxNG is expected to run on localhost -- this must not reject it."""

    client = HttpSearxngWebSearchClient("http://localhost:8080")
    assert client._base_url == "http://localhost:8080"


def test_client_rejects_bad_scheme_base_url():
    with pytest.raises(PresenceRejected):
        HttpSearxngWebSearchClient("ftp://searx.internal:8080")


def test_client_rejects_base_url_with_credentials():
    with pytest.raises(PresenceRejected):
        HttpSearxngWebSearchClient("http://user:pass@searx.internal:8080")


def test_client_rejects_blank_base_url():
    with pytest.raises(PresenceRejected):
        HttpSearxngWebSearchClient("")


def test_client_returns_empty_on_malformed_json():
    client = HttpSearxngWebSearchClient(
        "http://searx.internal:8080", opener=_opener(b"not json")
    )

    assert client.search("anything") == ()


def test_client_returns_empty_when_results_key_missing():
    body = json.dumps({"other": []}).encode("utf-8")
    client = HttpSearxngWebSearchClient("http://searx.internal:8080", opener=_opener(body))

    assert client.search("anything") == ()


def test_client_from_env_needs_base_url(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    assert client_from_env() is None

    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "http://searx.internal:8080")
    assert isinstance(client_from_env(), HttpSearxngWebSearchClient)


def test_client_from_env_accepts_localhost(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "http://localhost:8080")
    assert isinstance(client_from_env(), HttpSearxngWebSearchClient)


def test_fetch_page_text_extracts_visible_text():
    html = b"<html><body><h1>Contact</h1><p>Email us at jane@example.com</p></body></html>"

    text = fetch_page_text(
        "https://forum.example/thread/1",
        opener=_opener(html, url="https://forum.example/thread/1"),
        host_ok=lambda host: True,
    )

    assert "jane@example.com" in text


def test_fetch_page_text_rejects_private_host():
    with pytest.raises(PresenceRejected):
        fetch_page_text(
            "https://internal.example/",
            opener=_opener(b"<html></html>"),
            host_ok=lambda host: False,
        )
