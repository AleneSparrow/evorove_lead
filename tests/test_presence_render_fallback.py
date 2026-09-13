"""HttpPresenceSource's fallback to a headless render for thin (SPA) pages."""

from evorove_lead.business import BusinessSeed
from evorove_lead.presence import HttpPresenceSource, RenderRejected

SPA_SHELL_HTML = '<html><head><title>Sunrise Bakery</title></head><body><div id="root"></div></body></html>'
RENDERED_HTML = (
    "<html><body><h1>Weekend catering for local events</h1>"
    "<p>We cook for busy parents across the whole city, every weekend of the year, rain or shine.</p>"
    "</body></html>"
)


class _FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self._body = body
        self._url = url

    def read(self, size: int) -> bytes:
        return self._body[:size]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def _opener(html: str):
    def opener(request, timeout):  # noqa: ANN001
        return _FakeResponse(html.encode("utf-8"), request.full_url)

    return opener


SITE = "https://sunrise-bakery.example/"


def test_falls_back_to_render_when_plain_fetch_is_too_thin():
    renderer_calls = []

    def renderer(url: str) -> str:
        renderer_calls.append(url)
        return RENDERED_HTML

    source = HttpPresenceSource(
        opener=_opener(SPA_SHELL_HTML), host_ok=lambda host: True, renderer=renderer
    )

    materials = source.load(BusinessSeed(site_url=SITE))

    assert renderer_calls == [SITE]
    assert "Weekend catering for local events" in materials[0].body


def test_does_not_render_when_plain_fetch_already_has_enough_text():
    rich_html = (
        "<html><body><h1>Weekend catering for local events</h1>"
        "<p>We cook for busy parents across the whole city, every single weekend, rain or shine, "
        "and we have done so for the last ten years without missing a single Saturday.</p>"
        "</body></html>"
    )

    def renderer(url: str) -> str:
        raise AssertionError("must not render when the plain fetch is already rich enough")

    source = HttpPresenceSource(
        opener=_opener(rich_html), host_ok=lambda host: True, renderer=renderer
    )

    materials = source.load(BusinessSeed(site_url=SITE))

    assert "Weekend catering for local events" in materials[0].body


def test_keeps_the_plain_fetch_when_rendering_fails():
    def renderer(url: str) -> str:
        raise RenderRejected("playwright is not installed")

    source = HttpPresenceSource(
        opener=_opener(SPA_SHELL_HTML), host_ok=lambda host: True, renderer=renderer
    )

    materials = source.load(BusinessSeed(site_url=SITE))

    # Whatever the (thin) plain fetch got is still returned -- not an
    # exception -- when the render fallback itself fails.
    assert materials[0].name == SITE


def test_keeps_the_plain_fetch_when_render_is_not_actually_richer():
    def renderer(url: str) -> str:
        return SPA_SHELL_HTML  # still just the shell -- nothing gained

    source = HttpPresenceSource(
        opener=_opener(SPA_SHELL_HTML), host_ok=lambda host: True, renderer=renderer
    )

    materials = source.load(BusinessSeed(site_url=SITE))

    assert materials[0].name == SITE
