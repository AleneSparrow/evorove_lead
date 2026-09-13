"""A self-hosted metasearch client -- not a paid third-party search API.

The owner points `WEB_SEARCH_BASE_URL` at her own SearxNG instance (open
source, self-hosted; https://docs.searxng.org/) or anything returning the
same `{"results": [{"url": ..., "content": ...}, ...]}` JSON shape. This
module never bakes in a vendor's key, quota, or ToS assumption -- if that
shape doesn't match what the owner runs, only `_parse_results` changes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol, Sequence
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from evorove_lead.presence import (
    FETCH_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    USER_AGENT,
    PresenceRejected,
    _host_is_public,
    html_to_page_text,
    validate_public_http_url,
)

SEARCH_TIMEOUT_SECONDS = 10


def _validate_configured_base_url(url: str) -> str:
    """Light check for an owner-configured endpoint -- same trust level as
    `DATABASE_URL`/`CRM_BASE_URL`, not a URL discovered via search results.

    Deliberately does NOT require a public host: `validate_public_http_url`
    (used below in `fetch_page_text`, and for every URL a search result
    hands back) exists to stop SSRF against untrusted, discovered URLs. A
    self-hosted SearxNG instance is the opposite case -- it is expected to
    run on localhost or an internal address the owner configured herself.
    """

    raw = (url or "").strip()
    if not raw:
        raise PresenceRejected("search base URL is required")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise PresenceRejected("search base URL must be http(s)")
    if parsed.username or parsed.password:
        raise PresenceRejected("search base URL must not contain credentials")
    if not parsed.hostname:
        raise PresenceRejected("search base URL must have a host")
    return raw
MAX_RESULTS_PER_QUERY = 5


@dataclass(frozen=True)
class SearchHit:
    """One raw result from the search endpoint. Not yet a trace or a person."""

    url: str
    snippet: str


class WebSearchClient(Protocol):
    def search(self, query: str) -> Sequence[SearchHit]:
        """Return a handful of public results for this query. Empty is allowed."""


def _parse_results(body: bytes) -> tuple[SearchHit, ...]:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return ()
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return ()
    hits: list[SearchHit] = []
    for item in results[:MAX_RESULTS_PER_QUERY]:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        hits.append(SearchHit(url=url, snippet=(item.get("content") or "").strip()))
    return tuple(hits)


class HttpSearxngWebSearchClient:
    """Query a SearxNG-shaped JSON search endpoint the owner runs herself."""

    def __init__(self, base_url: str, opener=urlopen) -> None:
        self._base_url = _validate_configured_base_url(base_url).rstrip("/")
        self._opener = opener

    def search(self, query: str) -> Sequence[SearchHit]:
        query_string = urlencode({"q": query, "format": "json"})
        url = f"{self._base_url}/search?{query_string}"
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_BODY_BYTES + 1)
        except (URLError, TimeoutError, OSError):
            return ()
        if len(body) > MAX_BODY_BYTES:
            return ()
        return _parse_results(body)


def client_from_env() -> WebSearchClient | None:
    """No `WEB_SEARCH_BASE_URL` -> no client. The engine treats that as unconnected."""

    base_url = (os.getenv("WEB_SEARCH_BASE_URL") or "").strip()
    if not base_url:
        return None
    try:
        return HttpSearxngWebSearchClient(base_url)
    except PresenceRejected:
        return None


def fetch_page_text(url: str, opener=urlopen, host_ok=_host_is_public) -> str:
    """Fetch a public http(s) page's visible text. Same SSRF guard as `presence.py`."""

    validated = validate_public_http_url(url)
    host = urlparse(validated).hostname or ""
    if not host_ok(host):
        raise PresenceRejected("result URL host is not a public site")
    request = Request(validated, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            final_url = getattr(response, "geturl", lambda: validated)()
            if urlparse(final_url).scheme not in {"http", "https"}:
                raise PresenceRejected("result redirected off http(s)")
            body = response.read(MAX_BODY_BYTES + 1)
    except (URLError, TimeoutError, OSError) as exc:
        raise PresenceRejected("result page could not be read") from exc
    if len(body) > MAX_BODY_BYTES:
        raise PresenceRejected("result page is too large")
    html = body.decode("utf-8", errors="replace")
    _, _, body_text = html_to_page_text(html)
    return body_text
