"""Company names and links from a 'top N' list page. Facts only, no descriptions."""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from evorove_lead.domain_blocklist import block_reason
from evorove_lead.platforms import is_platform, registrable_domain

_LIST_TITLE = re.compile(
    r"\b(?:top|best)\s+\d+\b.{0,48}\b(?:compan(?:y|ies)|agenc(?:y|ies)|firms?|providers?|distributors?)\b"
    r"|\b\d+\s+(?:best|top)\b.{0,48}\b(?:compan(?:y|ies)|agenc(?:y|ies)|firms?|providers?|distributors?)\b",
    re.I,
)
_TAG = re.compile(r"<[^>]+>")
_SKIP_TEXT = frozenset(
    "read more learn more visit website website click here home about contact privacy".split()
)


def is_list_page(title: str, snippet: str = "") -> bool:
    return bool(_LIST_TITLE.search(f"{title or ''} {snippet or ''}"))


def list_company_limit() -> int:
    raw = (os.getenv("LIST_PAGE_COMPANY_LIMIT") or "12").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 12
    return min(30, max(1, value))


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []
        self._href = ""
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = {((name or "").lower()): (value or "") for name, value in attrs}
        self._href = attr.get("href", "").strip()
        self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            self._href = ""
            self._chunks = []
            return
        text = " ".join(" ".join(self._chunks).split())
        self.found.append((self._href, text))
        self._href = ""
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._chunks.append(data)


def companies_from_list_html(html: str, page_url: str, *, limit: int | None = None) -> tuple[tuple[str, str], ...]:
    """(name, absolute url) for companies linked off this list. Descriptions are not returned."""
    parser = _Anchors()
    parser.feed(html or "")
    parser.close()
    page_host = (urlsplit(page_url).hostname or "").casefold()
    page_domain = registrable_domain(page_host)
    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    cap = limit if limit is not None else list_company_limit()
    for href, text in parser.found:
        name = _TAG.sub(" ", text)
        name = " ".join(name.split())
        if not _name_ok(name):
            continue
        absolute = urljoin(page_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        host = parts.hostname.casefold()
        domain = registrable_domain(host)
        if not domain or domain == page_domain or domain in seen:
            continue
        if block_reason(host) or is_platform(host) or is_platform(domain):
            continue
        seen.add(domain)
        kept.append((name, f"{parts.scheme}://{host}{parts.path or '/'}"))
        if len(kept) >= cap:
            break
    return tuple(kept)


def _name_ok(name: str) -> bool:
    if not name or len(name) > 80 or "." in name:
        return False
    words = name.split()
    if not 1 <= len(words) <= 8:
        return False
    if name.casefold() in _SKIP_TEXT:
        return False
    letters = [character for character in name if character.isalpha()]
    return len(letters) >= 3
