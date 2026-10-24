"""Owner-site fetch, result pages, and Playwright share the evidence SSRF rule."""

from urllib.request import Request

import pytest

from evorove_lead.business import BusinessSeed
from evorove_lead.presence import (
    HttpPresenceSource,
    PresenceRejected,
    PublicRedirectHandler,
    require_public_http_url,
    validate_public_http_url,
)
from evorove_lead.rendering import RenderRejected, guard_render_url, render_page_html
from evorove_lead.web_search import fetch_page_text


class _FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self._body = body
        self._url = url

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


HTML = b"<html><body><h1>Weekend catering</h1><p>For busy parents across the city every weekend.</p></body></html>"
SITE = "https://sunrise-bakery.example/"


def test_require_public_http_url_rejects_metadata_and_loopback():
    for url in (
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/",
        "http://metadata.internal/",
        "file:///tmp/spa.html",
    ):
        with pytest.raises(PresenceRejected):
            require_public_http_url(url, host_ok=lambda host: True)


def test_public_redirect_rejects_private_and_metadata_hops():
    handler = PublicRedirectHandler(host_ok=lambda host: True)
    req = Request(SITE)
    for hop in (
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/",
        "http://10.0.0.8/admin",
    ):
        with pytest.raises(PresenceRejected):
            handler.redirect_request(req, None, 302, "Found", {}, hop)


def test_public_redirect_allows_another_public_http_hop():
    handler = PublicRedirectHandler(host_ok=lambda host: True)
    nxt = handler.redirect_request(Request(SITE), None, 302, "Found", {}, "https://cdn.example/page")
    assert nxt is not None
    assert nxt.full_url == "https://cdn.example/page"


def test_presence_rejects_private_final_url():
    def opener(request, timeout):
        return _FakeResponse(HTML, "http://127.0.0.1/")

    source = HttpPresenceSource(opener=opener, host_ok=lambda host: True, renderer=lambda url: None)
    with pytest.raises(PresenceRejected):
        source.load(BusinessSeed(site_url=SITE))


def test_presence_rejects_metadata_final_url():
    def opener(request, timeout):
        return _FakeResponse(HTML, "http://169.254.169.254/latest/meta-data")

    source = HttpPresenceSource(opener=opener, host_ok=lambda host: True, renderer=lambda url: None)
    with pytest.raises(PresenceRejected):
        source.load(BusinessSeed(site_url=SITE))


def test_fetch_page_text_rejects_private_redirect_target():
    def opener(request, timeout):
        return _FakeResponse(HTML, "http://10.1.2.3/internal")

    with pytest.raises(PresenceRejected):
        fetch_page_text(SITE, opener=opener, host_ok=lambda host: True)


def test_render_guard_matches_evidence_fetcher():
    for url in (
        "file:///tmp/spa.html",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/",
    ):
        with pytest.raises(RenderRejected):
            guard_render_url(url)
    assert validate_public_http_url(SITE) == SITE


def test_render_page_html_rejects_private_before_browser():
    with pytest.raises(RenderRejected):
        render_page_html("file:///tmp/spa.html")
    with pytest.raises(RenderRejected):
        render_page_html("http://127.0.0.1:1/definitely-not-listening")


def test_rejects_ipv6_loopback_mapped_userinfo_and_http_to_file():
    for url in (
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://a@127.0.0.1/",
        "http://example.com@127.0.0.1/",
    ):
        with pytest.raises(PresenceRejected):
            require_public_http_url(url, host_ok=lambda host: True)
    handler = PublicRedirectHandler(host_ok=lambda host: True)
    with pytest.raises(PresenceRejected):
        handler.redirect_request(Request(SITE), None, 302, "Found", {}, "file:///etc/passwd")


def test_rejects_decimal_hex_short_and_octal_loopback_without_dns():
    """inet_aton maps these to 127.0.0.1; the name check must not wait for DNS."""

    for url in (
        "http://2130706433/",
        "http://0x7f000001/",
        "http://127.1/",
        "http://0177.0.0.1/",
    ):
        with pytest.raises(PresenceRejected):
            validate_public_http_url(url)
        with pytest.raises(PresenceRejected):
            require_public_http_url(url, host_ok=lambda host: True)


def test_host_is_public_rejects_dns_answer_on_private_address(monkeypatch):
    from evorove_lead import presence

    monkeypatch.setattr(
        presence.socket,
        "getaddrinfo",
        lambda host, port: [(0, 0, 0, "", ("127.0.0.1", 0))],
    )
    assert presence._host_is_public("public-looking.example") is False
    with pytest.raises(PresenceRejected):
        require_public_http_url("https://public-looking.example/")
