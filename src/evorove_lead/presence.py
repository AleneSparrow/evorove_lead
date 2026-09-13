"""Read the owner's public site. Not third-party ad libraries, not secrets."""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from evorove_lead.business import BusinessSeed
from evorove_lead.materials import DepositedMaterial

MAX_BODY_BYTES = 1_000_000
FETCH_TIMEOUT_SECONDS = 10
USER_AGENT = "EvoroveLead/0.1 (cycle-1 business presence)"


class PresenceRejected(ValueError):
    """The engine will not treat this as the owner's public site."""


class RenderRejected(ValueError):
    """`rendering.py` could not headless-render the page. Defined here (not
    there) so `rendering.py` can import `USER_AGENT` from this module
    without a circular import back."""


class PresenceSource(Protocol):
    def load(self, seed: BusinessSeed) -> tuple[DepositedMaterial, ...]:
        """Return the business's own words, named by source."""


def _collapse(text: str) -> str:
    return " ".join(text.split())


# Closing one of these tags ends a sentence-like unit of visible text.
# Without this, two unrelated block elements ("<h1>Weekend catering for
# local events</h1><p>Evorove serves small businesses...</p>") join into
# one grammatical run with nothing but a space between them, and a
# downstream sentence-scoped heuristic (offer_reader.py's audience-phrase
# extraction) can bridge straight across the boundary as if it were one
# sentence.
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "tr", "blockquote", "br",
    }
)


class PageParser(HTMLParser):
    """Visible title, headings, and body from the owner's HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.headings: list[str] = []
        self._parts: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_heading = False
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._skip += 1
        elif self._skip:
            return
        elif lowered == "title":
            self._in_title = True
        elif lowered in {"h1", "h2"}:
            self._in_heading = True
            self._heading_buf = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if lowered == "title":
            self._in_title = False
            return
        if lowered in {"h1", "h2"} and self._in_heading:
            heading = _collapse(" ".join(self._heading_buf))
            if heading:
                self.headings.append(heading)
            self._in_heading = False
            self._heading_buf = []
        if lowered in _BLOCK_TAGS and self._parts and self._parts[-1] != ".":
            self._parts.append(".")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        chunk = " ".join(data.split())
        if not chunk:
            return
        if self._in_title:
            self.title = _collapse(f"{self.title} {chunk}")
        if self._in_heading:
            self._heading_buf.append(chunk)
        self._parts.append(chunk)

    @property
    def body(self) -> str:
        return _collapse(" ".join(self._parts))


def html_to_page_text(html: str) -> tuple[str, tuple[str, ...], str]:
    parser = PageParser()
    parser.feed(html)
    parser.close()
    return parser.title, tuple(parser.headings), parser.body


def page_material(url: str, html: str) -> DepositedMaterial:
    title, headings, body = html_to_page_text(html)
    # Headings first so the offer reader quotes the service, not the tab title.
    blocks = [piece for piece in (*headings, title, body) if piece]
    return DepositedMaterial(name=url, body="\n".join(blocks))


def validate_public_http_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise PresenceRejected("site URL is required")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise PresenceRejected("only http(s) site URLs are allowed")
    if parsed.username or parsed.password:
        raise PresenceRejected("site URL must not contain credentials")
    host = (parsed.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local"):
        raise PresenceRejected("site URL host is not a public site")
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None and (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_reserved
        or parsed_ip.is_multicast
    ):
        raise PresenceRejected("site URL host is not a public site")
    return raw


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise PresenceRejected("site URL host could not be resolved") from exc
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            parsed_ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            parsed_ip.is_private
            or parsed_ip.is_loopback
            or parsed_ip.is_link_local
            or parsed_ip.is_reserved
            or parsed_ip.is_multicast
        ):
            return False
    return True


# A client-rendered SPA serves a near-empty `<div id="root"></div>` shell
# over plain HTTP -- evorove.com itself is one (see rendering.py). Below
# this many characters of extracted body text, it's worth trying a
# headless render instead of accepting a brief built on almost nothing.
MIN_PLAIN_FETCH_TEXT_CHARS = 150


def _default_renderer(url: str) -> str:
    from evorove_lead.rendering import render_page_html

    return render_page_html(url)


class HttpPresenceSource:
    """Fetch the owner's public site. Tests inject a fake opener or presence.

    Falls back to a headless-rendered fetch (`rendering.py`) when the
    plain fetch's extracted text is too thin to be a real brief -- never
    instead of the plain fetch, which is faster and sufficient for most
    sites. Rendering is best-effort: any failure there (Playwright not
    installed, a browser crash, a timeout) just means this keeps
    whatever the plain fetch already got, same as if rendering had never
    been tried.
    """

    def __init__(
        self,
        opener=urlopen,
        host_ok=_host_is_public,
        renderer=None,
        min_plain_fetch_text_chars: int = MIN_PLAIN_FETCH_TEXT_CHARS,
    ) -> None:
        self._opener = opener
        self._host_ok = host_ok
        self._renderer = renderer
        self._min_plain_fetch_text_chars = min_plain_fetch_text_chars

    def load(self, seed: BusinessSeed) -> tuple[DepositedMaterial, ...]:
        url = validate_public_http_url(seed.site_url)
        host = urlparse(url).hostname or ""
        if not self._host_ok(host):
            raise PresenceRejected("site URL host is not a public site")

        material = page_material(url, self._fetch(url))
        if len(material.body) < self._min_plain_fetch_text_chars:
            rendered_html = self._try_render(url)
            if rendered_html is not None:
                rendered_material = page_material(url, rendered_html)
                if len(rendered_material.body) > len(material.body):
                    material = rendered_material
        return (material,)

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                final_url = getattr(response, "geturl", lambda: url)()
                if urlparse(final_url).scheme not in {"http", "https"}:
                    raise PresenceRejected("site redirected off http(s)")
                body = response.read(MAX_BODY_BYTES + 1)
        except (URLError, TimeoutError, OSError) as exc:
            raise PresenceRejected("owner site could not be read") from exc
        if len(body) > MAX_BODY_BYTES:
            raise PresenceRejected("owner site is too large")
        return body.decode("utf-8", errors="replace")

    def _try_render(self, url: str) -> str | None:
        renderer = self._renderer or _default_renderer
        try:
            return renderer(url)
        except RenderRejected:
            return None
