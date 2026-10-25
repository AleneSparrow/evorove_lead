"""Niche-independent fallback: SearxNG queries, then the company's own public pages."""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urlsplit
from evorove_lead.page_extract import extract_html
from evorove_lead.platforms import is_platform, registrable_domain
from evorove_lead.presence import PresenceRejected, require_public_http_url
from evorove_lead.source_adapters import RawSignal

def _when(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class UniversalWebFallback:
    """One adapter for every niche. It does not know what the business sells.

    Discovered pages go through `require_public_http_url` and `PublicSourceHTTP`,
    which uses `public_urlopen` and robots.txt. The SearxNG base URL is the
    owner's configured endpoint and may be localhost; result URLs may not.
    """

    source_id = "searxng_web"

    def __init__(self, http, search, queries: tuple[str, ...]):
        self.http, self.search, self.queries = http, search, tuple(queries)

    def fetch(self) -> tuple[RawSignal, ...]:
        if self.search is None:
            raise RuntimeError("search_not_configured")
        signals = []
        seen = set()
        for query in self.queries:
            for hit in self.search.search(query):
                try:
                    url = require_public_http_url(getattr(hit, "url", ""))
                except (PresenceRejected, ValueError):
                    continue
                host = urlsplit(url).hostname or ""
                domain = registrable_domain(host)
                if not domain or is_platform(host) or domain in seen:
                    continue
                quote = " ".join((getattr(hit, "snippet", "") or "").split())
                if not quote:
                    continue
                when = _when(getattr(hit, "published", ""))
                if when is None:
                    try:
                        page = extract_html(url, self.http.get(url))
                    except Exception:
                        continue
                    when = _when(page.publication_date or "")
                if when is None:
                    continue
                seen.add(domain)
                signals.append(RawSignal(company_name=domain[:120], person_name="", site_url=url,
                    signal_type="web_mention", signal_date=when.astimezone(timezone.utc), source_url=url,
                    quote=quote[:700], source=self.source_id, source_text=quote, source_id=self.source_id))
        return tuple(signals)
