"""Niche-independent fallback: SearxNG queries, then the company's own public pages."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit
from evorove_lead.company_facts import company_root, looks_english
from evorove_lead.domain_blocklist import block_reason
from evorove_lead.platforms import is_platform
from evorove_lead.presence import PresenceRejected, require_public_http_url
from evorove_lead.source_adapters import RawSignal


class UniversalWebFallback:
    """One adapter for every niche. It does not know what the business sells.

    Search hits are filtered before any company page is fetched. The candidate
    is the registrable domain's https root, not the nested result URL.
    Discovered pages go through `require_public_http_url` and `PublicSourceHTTP`,
    which uses `public_urlopen` and robots.txt. The SearxNG base URL is the
    owner's configured endpoint and may be localhost; result URLs may not.
    """

    source_id = "searxng_web"

    def __init__(self, http, search, queries: tuple[str, ...]):
        self.http, self.search, self.queries = http, search, tuple(queries)
        self.dropped: Counter = Counter()
        self.kept = 0

    def filter_report(self) -> dict:
        return {"kept": self.kept, "dropped": dict(self.dropped)}

    def fetch(self) -> tuple[RawSignal, ...]:
        if self.search is None:
            raise RuntimeError("search_not_configured")
        signals = []
        seen = set()
        for query in self.queries:
            for hit in self.search.search(query):
                signal = self._candidate(hit, seen)
                if signal is not None:
                    signals.append(signal)
        return tuple(signals)

    def _candidate(self, hit, seen: set) -> RawSignal | None:
        raw_url = getattr(hit, "url", "") or ""
        try:
            url = require_public_http_url(raw_url)
        except (PresenceRejected, ValueError):
            self.dropped["private_or_invalid"] += 1
            return None
        rooted = company_root(url)
        if rooted is None:
            self.dropped["private_or_invalid"] += 1
            return None
        domain, root = rooted
        host = urlsplit(url).hostname or ""
        reason = block_reason(host)
        if reason:
            self.dropped[reason] += 1
            return None
        if is_platform(host) or is_platform(domain):
            self.dropped["platform"] += 1
            return None
        text = " ".join(f"{getattr(hit, 'title', '') or ''} {getattr(hit, 'snippet', '') or ''}".split())
        if not looks_english(text):
            self.dropped["non_english"] += 1
            return None
        if domain in seen:
            self.dropped["duplicate"] += 1
            return None
        quote = " ".join((getattr(hit, "snippet", "") or "").split())
        if not quote:
            self.dropped["no_snippet"] += 1
            return None
        seen.add(domain)
        self.kept += 1
        # The search hit is only how we found the domain. A sales signal, and
        # any event date, is read later from the company's own pages.
        return RawSignal(
            company_name=domain[:120], person_name="", site_url=root,
            signal_type="company_candidate", signal_date=datetime.now(timezone.utc),
            source_url=root, quote=quote[:700], source=self.source_id, source_text=quote,
            context_only=True, source_id=self.source_id)
