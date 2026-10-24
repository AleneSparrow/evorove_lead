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
from urllib.error import HTTPError, URLError
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


class SearchError(RuntimeError):
    """Sanitized transport outcome; never includes endpoint, query or response body."""
    def __init__(self, code, *, retryable=False, retry_after=None, hits=()):
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after
        self.hits = tuple(hits)


def _parse_results(body: bytes) -> tuple[SearchHit, ...]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SearchError("search_invalid_json") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SearchError("search_invalid_structure")
    hits = []
    for item in payload["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"].strip() or not isinstance(item.get("content", ""), str):
            raise SearchError("search_invalid_structure")
        if len(hits) < MAX_RESULTS_PER_QUERY:
            hits.append(SearchHit(url=item["url"].strip(), snippet=item.get("content", "").strip()))
    failures = payload.get("unresponsive_engines", [])
    if not isinstance(failures, list):
        raise SearchError("search_invalid_structure")
    if failures:
        raise SearchError("search_sources_unresponsive", hits=hits)
    return tuple(hits)


class HttpSearxngWebSearchClient:
    """One HTTP attempt per call. BudgetedSearch alone owns transient retries."""
    def __init__(self, base_url: str, opener=None, *, max_requests=30):
        self._base_url = _validate_configured_base_url(base_url).rstrip("/")
        self._opener = opener or urlopen
        self.max_requests, self.requests = max_requests, 0
        self._cache = {}

    def search(self, query: str) -> Sequence[SearchHit]:
        if query in self._cache:
            return self._cache[query]
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise SearchError("search_request_limit")
        self.requests += 1
        request = Request(f"{self._base_url}/search?{urlencode({'q': query, 'format': 'json'})}", headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise self._http_error(status, getattr(response, "headers", {}))
                body = response.read(MAX_BODY_BYTES + 1)
        except HTTPError as exc:
            raise self._http_error(exc.code, exc.headers) from None
        except TimeoutError:
            raise SearchError("search_timeout", retryable=True) from None
        except (URLError, OSError):
            raise SearchError("search_network_error", retryable=True) from None
        if len(body) > MAX_BODY_BYTES:
            raise SearchError("search_response_too_large")
        hits = _parse_results(body)
        self._cache[query] = hits
        return hits

    @staticmethod
    def _http_error(status, headers):
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        raw = (headers or {}).get("Retry-After")
        delay = None
        if raw:
            try:
                delay = max(0., float(raw))
            except (ValueError, TypeError):
                try:
                    delay = max(0., (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return SearchError(f"search_http_{status}", retryable=status in {429, 500, 502, 503, 504}, retry_after=delay)


def client_from_env(*, required=False) -> WebSearchClient | None:
    base_url = (os.getenv("WEB_SEARCH_BASE_URL") or "").strip()
    if not base_url:
        if required or os.getenv("RESEARCH_ENABLED") == "1":
            raise SearchError("search_not_configured")
        return None
    try:
        raw_limit = os.getenv("LEAD_MAX_SEARCH_REQUESTS", "30")
        limit = int(raw_limit)
        if limit < 0:
            raise ValueError("negative limit")
        return HttpSearxngWebSearchClient(base_url, max_requests=limit or None)
    except (PresenceRejected, ValueError):
        raise SearchError("search_invalid_configuration") from None


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


def fetch_source_snapshot(url: str, business_id: str, subject: str):
    """Fetch evidence with guarded redirects and explicitly observed publication metadata.

    Search snippets are never substituted for an unavailable page.
    """
    import re
    from datetime import datetime
    from urllib.request import HTTPRedirectHandler, build_opener
    from html.parser import HTMLParser
    from evorove_lead.evidence import SourceSnapshot, utcnow, digest

    class SafeRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate_public_http_url(newurl)
            if not _host_is_public(urlparse(newurl).hostname or ""):
                raise PresenceRejected("redirect host is not public")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class Dates(HTMLParser):
        published = None
        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            if tag == "meta" and (data.get("property") or data.get("name")) in {"article:published_time", "datePublished"}:
                try:
                    value = datetime.fromisoformat((data.get("content") or "").replace("Z", "+00:00"))
                    if value.tzinfo: self.published = value
                except ValueError: pass

    validated = validate_public_http_url(url)
    host = urlparse(validated).hostname or ""
    if host.endswith("linkedin.com") or ("google" in host and "ads" in validated):
        raise PresenceRejected("restricted source")
    if not _host_is_public(host): raise PresenceRejected("source host is not public")
    try:
        with build_opener(SafeRedirect()).open(Request(validated, headers={"User-Agent": USER_AGENT}), timeout=FETCH_TIMEOUT_SECONDS) as response:
            final = validate_public_http_url(response.geturl())
            body = response.read(MAX_BODY_BYTES + 1)
            status = response.status
    except (URLError, TimeoutError, OSError) as exc:
        raise PresenceRejected("source unavailable") from exc
    if len(body) > MAX_BODY_BYTES: raise PresenceRejected("source too large")
    html = body.decode("utf-8", errors="replace")
    _, _, text = html_to_page_text(html)
    if len(text.strip()) < 80: raise PresenceRejected("insufficient public text")
    if re.search(r"sign in to (continue|read)|subscribe to (continue|read)|enable cookies to continue", text, re.I):
        raise PresenceRejected("access restricted")
    dates = Dates()
    dates.feed(html)
    now = utcnow()
    return SourceSnapshot(id=digest(final + now.isoformat()), business_id=business_id, url=validated, final_url=final,
                          content=text[:18000], fetched_at=now, published_at=dates.published, status_code=status,
                          source_type="first_party", subject=subject)
