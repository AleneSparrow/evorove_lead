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
from typing import Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from evorove_lead.presence import (
    FETCH_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    USER_AGENT,
    PresenceRejected,
    PublicRedirectHandler,
    _host_is_public,
    html_to_page_text,
    public_urlopen,
    require_public_http_url,
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
MAX_RESULTS_PER_PAGE = 10
MAX_RESULTS_PER_QUERY = 30


@dataclass(frozen=True)
class SearchHit:
    """One raw result from the search endpoint. Not yet a trace or a person."""

    url: str
    snippet: str
    published: str = ""
    title: str = ""
    engine: str = ""


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
        if len(hits) < MAX_RESULTS_PER_PAGE:
            published = item.get("publishedDate") or item.get("published") or ""
            title = item.get("title") or ""
            engine = item.get("engine") or ""
            if not engine and isinstance(item.get("engines"), list):
                engine = ",".join(part for part in item["engines"] if isinstance(part, str))
            hits.append(SearchHit(url=item["url"].strip(), snippet=item.get("content", "").strip(),
                published=published.strip() if isinstance(published, str) else "",
                title=title.strip() if isinstance(title, str) else "",
                engine=engine.strip() if isinstance(engine, str) else ""))
    failures = payload.get("unresponsive_engines", [])
    if not isinstance(failures, list):
        raise SearchError("search_invalid_structure")
    if failures and not hits:
        # Partial engine failure with zero hits is not "no results".
        raise SearchError("search_sources_unresponsive", retryable=True, retry_after=1.0)
    return tuple(hits)


class HttpSearxngWebSearchClient:
    """One HTTP attempt per page. BudgetedSearch alone owns transient retries."""
    def __init__(self, base_url: str, opener=None, *, max_pages: int = 1, max_requests=30, pause_seconds: float = 0):
        self._base_url = _validate_configured_base_url(base_url).rstrip("/")
        self._opener = opener or urlopen
        self._max_pages = max(1, max_pages)
        self.pause_seconds = max(0.0, pause_seconds)
        self.max_requests, self.requests = max_requests, 0
        self._cache = {}
        self.query_counts: dict[str, int] = {}

    def search(self, query: str) -> Sequence[SearchHit]:
        key = " ".join((query or "").split())
        if key in self._cache:
            return self._cache[key]
        if self.max_requests is not None and self.requests >= self.max_requests:
            if self._max_pages > 1:
                self.query_counts[key] = 0
                return ()
            raise SearchError("search_request_limit")
        collected: list[SearchHit] = []
        for page in range(1, self._max_pages + 1):
            if len(collected) >= MAX_RESULTS_PER_QUERY:
                break
            if self.max_requests is not None and self.requests >= self.max_requests:
                break
            if page > 1 and self.pause_seconds:
                time.sleep(self.pause_seconds)
            self.requests += 1
            params = {"q": query, "format": "json", "language": "en-US"}
            if page > 1:
                params["pageno"] = page
            request = Request(
                f"{self._base_url}/search?{urlencode(params)}",
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            body = self._fetch_body(request)
            page_hits = _parse_results(body)
            if not page_hits:
                break
            room = MAX_RESULTS_PER_QUERY - len(collected)
            collected.extend(page_hits[:room])
        hits = dedupe_hits(collected)
        self._cache[key] = hits
        self.query_counts[key] = len(hits)
        return hits

    def _fetch_body(self, request: Request) -> bytes:
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
        return body

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


def configure_playbook_search(client) -> None:
    """A playbook collect reads two or three result pages, with a pause between them."""
    if client is None or not hasattr(client, "_max_pages"):
        return
    try:
        pages = int(os.getenv("SEARCH_MAX_PAGES", "3"))
    except ValueError:
        pages = 3
    try:
        pause = float(os.getenv("SEARCH_PAGE_PAUSE_SECONDS", "0.4"))
    except ValueError:
        pause = 0.4
    client._max_pages = min(3, max(2, pages))
    client.pause_seconds = max(0.0, pause)


def client_from_env(*, required=False) -> WebSearchClient | None:
    base_url = (os.getenv("WEB_SEARCH_BASE_URL") or "").strip()
    if not base_url:
        if required or os.getenv("RESEARCH_ENABLED") == "1":
            raise SearchError("search_not_configured")
        return None
    raw_cap = (os.getenv("LEAD_MAX_SEARCH_REQUESTS") or "").strip()
    try:
        if raw_cap == "0":
            max_requests = None
        elif raw_cap:
            max_requests = int(raw_cap)
            if max_requests < 0:
                raise ValueError("negative limit")
        else:
            max_requests = 30
        return HttpSearxngWebSearchClient(base_url, max_requests=max_requests)
    except (PresenceRejected, ValueError):
        raise SearchError("search_invalid_configuration") from None


def fetch_page_text(url: str, opener=urlopen, host_ok=_host_is_public) -> str:
    """Fetch a public http(s) page's visible text. Same SSRF guard as `presence.py`."""

    validated = require_public_http_url(url, host_ok=host_ok)
    request = Request(validated, headers={"User-Agent": USER_AGENT})
    try:
        transport = opener if opener is not urlopen else public_urlopen(host_ok)
        with transport(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            final_url = getattr(response, "geturl", lambda: validated)()
            require_public_http_url(final_url, host_ok=host_ok)
            body = response.read(MAX_BODY_BYTES + 1)
    except PresenceRejected:
        raise
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
    from urllib.request import build_opener
    from html.parser import HTMLParser
    from evorove_lead.evidence import SourceSnapshot, utcnow, digest

    class Dates(HTMLParser):
        published = None
        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            if tag == "meta" and (data.get("property") or data.get("name")) in {"article:published_time", "datePublished"}:
                try:
                    value = datetime.fromisoformat((data.get("content") or "").replace("Z", "+00:00"))
                    if value.tzinfo: self.published = value
                except ValueError: pass

    validated = require_public_http_url(url)
    host = urlparse(validated).hostname or ""
    if host.endswith("linkedin.com") or ("google" in host and "ads" in validated):
        raise PresenceRejected("restricted source")
    try:
        with build_opener(PublicRedirectHandler()).open(Request(validated, headers={"User-Agent": USER_AGENT}), timeout=FETCH_TIMEOUT_SECONDS) as response:
            final = require_public_http_url(response.geturl())
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
