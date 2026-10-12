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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
    published_at: datetime | None = None


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
        hits.append(
            SearchHit(
                url=url,
                snippet=(item.get("content") or "").strip(),
                published_at=_published_at(item),
            )
        )
    return tuple(hits)


def _published_at(item: dict) -> datetime | None:
    raw = item.get("publishedDate") or item.get("published_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class HttpSearxngWebSearchClient:
    """Query a SearxNG-shaped JSON search endpoint the owner runs herself.

    Transport failures retry a bounded number of times and are not cached.
    A successful response, including a real empty result, is cached for this
    process so the same query is not billed twice in one run.
    """

    def __init__(
        self,
        base_url: str,
        opener=urlopen,
        *,
        max_attempts: int = 3,
        min_interval_seconds: float = 0.0,
        sleeper=time.sleep,
    ) -> None:
        self._base_url = _validate_configured_base_url(base_url).rstrip("/")
        self._opener = opener
        self._max_attempts = min(5, max(1, max_attempts))
        self._min_interval_seconds = max(0.0, min_interval_seconds)
        self._sleeper = sleeper
        self._cache: dict[str, tuple[SearchHit, ...]] = {}
        self._next_at = 0.0
        self.cache_hits = 0
        self.transport_failures = 0

    def search(self, query: str) -> Sequence[SearchHit]:
        cached = self._cache.get(query)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self._pace()
        for attempt in range(self._max_attempts):
            try:
                hits = self._fetch(query)
            except (URLError, TimeoutError, OSError):
                self.transport_failures += 1
                if attempt + 1 == self._max_attempts:
                    return ()
                self._sleeper(0.05 * (attempt + 1))
                continue
            self._cache[query] = hits
            return hits
        return ()

    def _fetch(self, query: str) -> tuple[SearchHit, ...]:
        query_string = urlencode({"q": query, "format": "json"})
        url = f"{self._base_url}/search?{query_string}"
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self._opener(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return ()
        return _parse_results(body)

    def _pace(self) -> None:
        if self._min_interval_seconds <= 0:
            return
        now = time.monotonic()
        wait = self._next_at - now
        if wait > 0:
            self._sleeper(wait)
        self._next_at = time.monotonic() + self._min_interval_seconds


def client_from_env() -> WebSearchClient | None:
    """No `WEB_SEARCH_BASE_URL` -> no client. The engine treats that as unconnected."""

    base_url = (os.getenv("WEB_SEARCH_BASE_URL") or "").strip()
    if not base_url:
        return None
    try:
        interval = float((os.getenv("LEAD_SEARCH_MIN_INTERVAL_SECONDS") or "0").strip() or "0")
    except ValueError:
        interval = 0.0
    try:
        attempts = int((os.getenv("LEAD_SEARCH_ATTEMPTS") or "3").strip() or "3")
    except ValueError:
        attempts = 3
    try:
        return HttpSearxngWebSearchClient(
            base_url, max_attempts=attempts, min_interval_seconds=interval
        )
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
