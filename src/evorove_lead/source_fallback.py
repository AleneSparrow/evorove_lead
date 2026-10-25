"""Niche-independent fallback: SearxNG queries, then the company's own public pages."""
from __future__ import annotations
import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit
from evorove_lead.company_facts import company_root, looks_english
from evorove_lead.competitor_offers import competitor_offer
from evorove_lead.domain_blocklist import block_reason
from evorove_lead.platforms import is_platform
from evorove_lead.presence import PresenceRejected, require_public_http_url
from evorove_lead.source_adapters import RawSignal


_AGGREGATOR = re.compile(r"\b(?:best|top\s+\d+|\d+\s+best|directory|list of|compared|roundup)\b", re.I)
CRAWL_LIMIT = 40


class UniversalWebFallback:
    """One adapter for every niche. It does not know what the business sells.

    Search hits are filtered before any company page is fetched. The candidate
    is the registrable domain's https root, not the nested result URL.
    Discovered pages go through `require_public_http_url` and `PublicSourceHTTP`,
    which uses `public_urlopen` and robots.txt. The SearxNG base URL is the
    owner's configured endpoint and may be localhost; result URLs may not.
    """

    source_id = "searxng_web"

    def __init__(self, http, search, queries: tuple[str, ...], terms: tuple[str, ...] = (), crawl_limit: int = CRAWL_LIMIT):
        self.http, self.search, self.queries = http, search, tuple(queries)
        self.terms = tuple(term.casefold() for term in terms)
        self.crawl_limit = crawl_limit
        self.dropped: Counter = Counter()
        self.kept = 0
        self.held_back = 0

    def filter_report(self) -> dict:
        return {"kept": self.kept, "held_back": self.held_back, "dropped": dict(self.dropped), "crawl_limit": self.crawl_limit}

    def fetch(self) -> tuple[RawSignal, ...]:
        if self.search is None:
            raise RuntimeError("search_not_configured")
        ranked = []
        seen = set()
        for query in self.queries:
            for position, hit in enumerate(self.search.search(query)):
                signal = self._candidate(hit, seen, position)
                if signal is not None:
                    ranked.append(signal)
        ranked.sort(key=lambda item: item[0])
        signals = [item[1] for item in ranked]
        self.held_back = max(0, len(signals) - self.crawl_limit)
        return tuple(signals[:self.crawl_limit])

    def _candidate(self, hit, seen: set, position: int) -> tuple | None:
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
        title = " ".join((getattr(hit, "title", "") or "").split())
        snippet = " ".join((getattr(hit, "snippet", "") or "").split())
        text = " ".join(f"{title} {snippet}".split())
        if not looks_english(text):
            self.dropped["non_english"] += 1
            return None
        if competitor_offer(text):
            self.dropped["competitor"] += 1
            return None
        if domain in seen:
            self.dropped["duplicate"] += 1
            return None
        if not snippet:
            self.dropped["no_snippet"] += 1
            return None
        seen.add(domain)
        self.kept += 1
        folded = title.casefold()
        type_miss = 0 if any(term in folded for term in self.terms) else 1
        aggregator = 1 if _AGGREGATOR.search(title) else 0
        # The search hit is only how we found the domain. A signal date is read
        # later from the company's own page and is never this collection clock.
        signal = RawSignal(
            company_name=domain[:120], person_name="", site_url=root,
            signal_type="company_candidate", signal_date=datetime.now(timezone.utc),
            source_url=root, quote=snippet[:700], source=self.source_id, source_text=snippet,
            context_only=True, source_id=self.source_id)
        return ((aggregator, type_miss, position, domain), signal)
