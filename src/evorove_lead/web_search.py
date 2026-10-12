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
    validate_public_http_url,
)
from evorove_lead.result_collect import dedupe_hits

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

    def __init__(
        self,
        base_url: str,
        opener=urlopen,
        *,
        max_pages: int = 1,
        max_requests: int | None = None,
    ) -> None:
        self._base_url = _validate_configured_base_url(base_url).rstrip("/")
        self._opener = opener
        self._max_pages = max(1, max_pages)
        self._max_requests = max_requests
        self._http_calls = 0
        self._cache: dict[str, tuple[SearchHit, ...]] = {}

    def search(self, query: str) -> Sequence[SearchHit]:
        key = " ".join((query or "").split())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        collected: list[SearchHit] = []
        for page in range(1, self._max_pages + 1):
            if self._max_requests is not None and self._http_calls >= self._max_requests:
                break
            body = self._fetch_page(key, page)
            self._http_calls += 1
            if body is None:
                break
            page_hits = _parse_results(body)
            if not page_hits:
                break
            collected.extend(page_hits)
        result = dedupe_hits(collected)
        self._cache[key] = result
        return result

    def _fetch_page(self, query: str, page: int) -> bytes | None:
        params = {"q": query, "format": "json"}
        if page > 1:
            params["pageno"] = page
        url = f"{self._base_url}/search?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self._opener(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_BODY_BYTES + 1)
        except (URLError, TimeoutError, OSError):
            return None
        if len(body) > MAX_BODY_BYTES:
            return None
        return body


def client_from_env() -> WebSearchClient | None:
    """No `WEB_SEARCH_BASE_URL` -> no client. The engine treats that as unconnected.

    ``LEAD_MAX_SEARCH_REQUESTS`` caps HTTP calls for the process-local client.
    Unset means 30, which is the pilot ceiling. ``0`` leaves the cap off.
    """

    base_url = (os.getenv("WEB_SEARCH_BASE_URL") or "").strip()
    if not base_url:
        return None
    raw_cap = (os.getenv("LEAD_MAX_SEARCH_REQUESTS") or "").strip()
    if raw_cap == "0":
        max_requests = None
    elif raw_cap:
        max_requests = int(raw_cap)
    else:
        max_requests = 30
    try:
        return HttpSearxngWebSearchClient(base_url, max_requests=max_requests)
    except PresenceRejected:
        return None


def fetch_page_text(url: str, opener=None, host_ok=_host_is_public) -> str:
    """Fetch a public http(s) page's visible text. Same SSRF guard as `presence.py`.

    The default opener checks each redirect. Passing ``urlopen`` would
    follow a hop to a private address before that check can run.
    """

    from evorove_lead.page_extract import fetch_extracted_page

    return fetch_extracted_page(url, opener=opener, host_ok=host_ok).text
