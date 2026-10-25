"""Extract the readable part of a public page. HTML is untrusted data.

Dates and authors are copied only from explicit markup. A missing date
stays empty. Script, navigation, and advertising chrome are dropped so
a later classifier sees the article, not the whole document.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from evorove_lead.presence import (
    FETCH_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    USER_AGENT,
    PresenceRejected,
    _host_is_public,
    require_public_http_url,
    validate_public_http_url,
)

_TEXT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "nav", "footer", "aside", "iframe", "svg", "form", "button"}
)
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
# Whole tokens only. A substring match treats class="downloads" as ads
# and class="unavailable" as navigation, then drops the article.
_CHROME_MARKERS = frozenset(
    {
        "nav",
        "navbar",
        "navigation",
        "menu",
        "footer",
        "cookie",
        "advert",
        "advertisement",
        "advertising",
        "ads",
        "sidebar",
        "breadcrumb",
        "popup",
    }
)
_CACHE_LIMIT = 128
_EXTRACT_CACHE: "OrderedDict[str, ExtractedPage]" = OrderedDict()


@dataclass(frozen=True)
class ExtractedPage:
    source_url: str
    canonical_url: str
    title: str
    text: str
    fragments: tuple[str, ...]
    publication_date: str | None
    author: str | None
    content_type: str
    language: str = ""

    @property
    def content_sha256(self) -> str:
        normalized = " ".join(self.text.split()).casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class _PublicRedirectHandler(HTTPRedirectHandler):
    """Follow a few redirects, and only to public http(s) URLs."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            validate_public_http_url(newurl)
            host = urlparse(newurl).hostname or ""
            if host:
                _host_is_public(host)
        except PresenceRejected:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def guarded_urlopen(request, timeout=None):  # noqa: ANN001
    opener = build_opener(_PublicRedirectHandler)
    return opener.open(request, timeout=timeout)


def _chrome(attrs: list[tuple[str, str | None]]) -> bool:
    blob = " ".join(f"{name or ''} {value or ''}" for name, value in attrs).casefold()
    tokens = set(re.findall(r"[a-z0-9]+", blob))
    return bool(tokens & _CHROME_MARKERS)


class _MainContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author: str | None = None
        self.publication_date: str | None = None
        self.language = ""
        self.canonical_href: str | None = None
        self._parts: list[str] = []
        self._skip_stack: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attr = {((name or "").lower()): (value or "") for name, value in attrs}
        if lowered == "html":
            self.language = (attr.get("lang") or "").strip()
        if self._skip_stack or lowered in _SKIP_TAGS or _chrome(attrs):
            if lowered not in _VOID_TAGS:
                self._skip_stack.append(lowered)
            return
        if lowered == "title":
            self._in_title = True
        elif lowered == "meta":
            self._meta(attr)
        elif lowered == "link" and "canonical" in attr.get("rel", "").casefold():
            href = attr.get("href", "").strip()
            if href:
                self.canonical_href = href
        elif lowered == "time":
            stamp = attr.get("datetime", "").strip()
            if stamp and self.publication_date is None:
                self.publication_date = stamp

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _VOID_TAGS:
            return
        if lowered in self._skip_stack:
            while self._skip_stack and self._skip_stack[-1] != lowered:
                self._skip_stack.pop()
            if self._skip_stack:
                self._skip_stack.pop()
            return
        if lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        chunk = " ".join(data.split())
        if not chunk:
            return
        if self._in_title:
            self.title = " ".join(f"{self.title} {chunk}".split())
            return
        self._parts.append(chunk)

    def _meta(self, attr: dict[str, str]) -> None:
        name = (attr.get("name") or attr.get("property") or attr.get("itemprop") or "").casefold()
        content = (attr.get("content") or "").strip()
        if not content:
            return
        if name in {"author", "article:author"} and self.author is None:
            self.author = content
        if name in {"article:published_time", "date", "pubdate", "dc.date"} and self.publication_date is None:
            self.publication_date = content

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def relevant_fragment(text: str, query: str = "", limit: int = 1500) -> str:
    """A short window around the query, with surrounding sentences, capped."""

    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    folded = normalized.casefold()
    index = -1
    for term in re.findall(r"[a-z0-9]{4,}", (query or "").casefold()):
        index = folded.find(term)
        if index >= 0:
            break
    if index < 0:
        return normalized[:limit]
    start = max(0, index - 400)
    return normalized[start : start + limit]


def extract_html(source_url: str, html: str, *, content_type: str = "text/html") -> ExtractedPage:
    parser = _MainContentParser()
    parser.feed(html or "")
    parser.close()
    text = parser.text
    canonical = source_url
    if parser.canonical_href:
        joined = urljoin(source_url, parser.canonical_href)
        parsed = urlparse(joined)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            canonical = joined
    fragment = relevant_fragment(text)
    fragments = (fragment,) if fragment else ()
    return ExtractedPage(
        source_url=source_url,
        canonical_url=canonical,
        title=parser.title,
        text=text,
        fragments=fragments,
        publication_date=parser.publication_date,
        author=parser.author,
        content_type=content_type,
        language=parser.language,
    )


def content_sha256(text: str) -> str:
    normalized = " ".join((text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _content_type(response) -> str:  # noqa: ANN001
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    if hasattr(headers, "get_content_type"):
        return (headers.get_content_type() or "").casefold()
    raw = headers.get("Content-Type") if hasattr(headers, "get") else ""
    return (raw or "").split(";", 1)[0].strip().casefold()


def _remember(cache: OrderedDict, key: str, page: ExtractedPage) -> None:
    cache[key] = page
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def fetch_extracted_page(url: str, opener=None, host_ok=_host_is_public, cache=None) -> ExtractedPage:
    """Fetch one public page. Redirect targets are checked. Private hosts are refused."""

    validated = require_public_http_url(url, host_ok=host_ok)
    store: OrderedDict = _EXTRACT_CACHE if cache is None else cache
    cached = store.get(validated)
    if cached is not None:
        return cached
    open_url = opener or guarded_urlopen
    request = Request(
        validated,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain"},
    )
    try:
        with open_url(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            final_url = getattr(response, "geturl", lambda: validated)()
            require_public_http_url(final_url, host_ok=host_ok)
            content_type = _content_type(response)
            if content_type and content_type not in _TEXT_TYPES:
                raise PresenceRejected("result page is not text or html")
            body = response.read(MAX_BODY_BYTES + 1)
    except PresenceRejected:
        raise
    except (URLError, TimeoutError, OSError) as exc:
        raise PresenceRejected("result page could not be read") from exc
    if len(body) > MAX_BODY_BYTES:
        raise PresenceRejected("result page is too large")
    page = extract_html(final_url, body.decode("utf-8", errors="replace"), content_type=content_type or "text/html")
    _remember(store, validated, page)
    return page


# urlopen is referenced so a caller can still pass the stdlib opener explicitly.
_ = urlopen
