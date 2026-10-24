"""Read the owner's public site. Not third-party ad libraries, not secrets."""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Callable, Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from evorove_lead.business import BusinessSeed
from evorove_lead.materials import DepositedMaterial

MAX_BODY_BYTES = 1_000_000
FETCH_TIMEOUT_SECONDS = 10
USER_AGENT = "EvoroveLead/0.1 (cycle-1 business presence)"
METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.google.com",
        "metadata.internal",
        "metadata.goog",
        "instance-data",
    }
)


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
    if (
        not host
        or host == "localhost"
        or host.endswith(".local")
        or host.endswith(".internal")
        or host in METADATA_HOSTS
    ):
        raise PresenceRejected("site URL host is not a public site")
    if _blocked_ip(_ip_from_host(host)):
        raise PresenceRejected("site URL host is not a public site")
    return raw


def _ip_from_host(host: str):
    """Parse a hostname as an IP, including decimal, hex, octal, and short IPv4."""

    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def _blocked_ip(parsed_ip) -> bool:
    if parsed_ip is None:
        return False
    mapped = getattr(parsed_ip, "ipv4_mapped", None)
    if mapped is not None:
        parsed_ip = mapped
    return (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_reserved
        or parsed_ip.is_multicast
        or parsed_ip.is_unspecified
    )


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise PresenceRejected("site URL host could not be resolved") from exc
    seen = False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        parsed_ip = _ip_from_host(sockaddr[0])
        if parsed_ip is None:
            continue
        seen = True
        if _blocked_ip(parsed_ip):
            return False
    return seen


def require_public_http_url(url: str, host_ok: Callable[[str], bool] | None = None) -> str:
    """Reject credentials, non-http(s), private, loopback, and metadata targets.

    Used by the owner-site fetch, evidence fetch, result-page fetch, and the
    Playwright fallback. A custom `host_ok` is for tests; production resolves
    the hostname and refuses any private address in the set.
    """

    validated = validate_public_http_url(url)
    checker = host_ok or _host_is_public
    host = urlparse(validated).hostname or ""
    if not checker(host):
        raise PresenceRejected("site URL host is not a public site")
    return validated


class PublicRedirectHandler(HTTPRedirectHandler):
    """Refuse a hop the moment it leaves the public internet."""

    def __init__(self, host_ok: Callable[[str], bool] | None = None) -> None:
        super().__init__()
        self.host_ok = host_ok or _host_is_public

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require_public_http_url(newurl, host_ok=self.host_ok)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_urlopen(host_ok: Callable[[str], bool] | None = None):
    opener = build_opener(PublicRedirectHandler(host_ok=host_ok or _host_is_public))
    return opener.open


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
        url = require_public_http_url(seed.site_url, host_ok=self._host_ok)

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
            opener = self._opener if self._opener is not urlopen else public_urlopen(self._host_ok)
            with opener(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                final_url = getattr(response, "geturl", lambda: url)()
                require_public_http_url(final_url, host_ok=self._host_ok)
                body = response.read(MAX_BODY_BYTES + 1)
        except PresenceRejected:
            raise
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
