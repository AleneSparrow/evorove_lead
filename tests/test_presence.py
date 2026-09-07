import pytest

from evorove_lead.business import BusinessSeed
from evorove_lead.presence import (
    HttpPresenceSource,
    PresenceRejected,
    page_material,
    validate_public_http_url,
)


BAKERY_HTML = (
    "<html><body><h1>Weekend catering</h1>"
    "<p>For busy parents.</p></body></html>"
)


def test_rejects_non_http_and_credential_urls() -> None:
    with pytest.raises(PresenceRejected, match="http"):
        validate_public_http_url("file:///tmp/site.html")
    with pytest.raises(PresenceRejected, match="credentials"):
        validate_public_http_url("https://user:pass@example.com/")
    with pytest.raises(PresenceRejected, match="public site"):
        validate_public_http_url("https://localhost/")
    with pytest.raises(PresenceRejected, match="public site"):
        validate_public_http_url("http://127.0.0.1/")


def test_page_material_keeps_heading_quote() -> None:
    material = page_material("https://sunrise-bakery.example/", BAKERY_HTML)
    assert "Weekend catering" in material.body
    assert material.name == "https://sunrise-bakery.example/"


class _FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self._body = body
        self._url = url

    def read(self, size: int) -> bytes:
        return self._body[:size]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_http_presence_reads_injected_response() -> None:
    def opener(request, timeout):  # noqa: ANN001
        assert request.full_url == "https://sunrise-bakery.example/"
        return _FakeResponse(BAKERY_HTML.encode("utf-8"), request.full_url)

    source = HttpPresenceSource(opener=opener, host_ok=lambda host: True)
    materials = source.load(BusinessSeed(site_url="https://sunrise-bakery.example/"))

    assert len(materials) == 1
    assert "Weekend catering" in materials[0].body
