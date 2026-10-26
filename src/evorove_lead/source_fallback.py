"""Niche-independent fallback: SearxNG queries, then the company's own public pages."""
from __future__ import annotations
import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit
from evorove_lead.company_facts import company_root, looks_english
from evorove_lead.competitor_offers import competitor_offer
from evorove_lead.domain_blocklist import block_reason
from evorove_lead.list_pages import companies_from_list_html, is_list_page, list_company_limit
from evorove_lead.platforms import is_platform
from evorove_lead.presence import PresenceRejected, require_public_http_url
from evorove_lead.source_adapters import RawSignal
from evorove_lead.web_search import SearchHit


_AGGREGATOR = re.compile(r"\b(?:best|top\s+\d+|\d+\s+best|directory|list of|compared|roundup)\b", re.I)
CRAWL_LIMIT = 80


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
        self.query_results: list[dict] = []
        self.companies_from_lists = 0
        self.found_queries: dict[str, str] = {}

    def filter_report(self) -> dict:
        return {
            "kept": self.kept, "held_back": self.held_back, "dropped": dict(self.dropped),
            "crawl_limit": self.crawl_limit, "query_results": list(self.query_results),
            "companies_from_lists": self.companies_from_lists,
        }

    def fetch(self) -> tuple[RawSignal, ...]:
        if self.search is None:
            raise RuntimeError("search_not_configured")
        ranked = []
        seen = set()
        for query in self.queries:
            hits = tuple(self.search.search(query))
            self.query_results.append({"query": query, "results": len(hits)})
            for position, hit in enumerate(hits):
                title = getattr(hit, "title", "") or ""
                snippet = getattr(hit, "snippet", "") or ""
                if is_list_page(title, snippet):
                    self._expand_list(hit, seen, position, ranked, query)
                    continue
                signal = self._candidate(hit, seen, position, found_query=query)
                if signal is not None:
                    ranked.append(signal)
        ranked.sort(key=lambda item: item[0])
        signals = [item[1] for item in ranked]
        self.held_back = max(0, len(signals) - self.crawl_limit)
        return tuple(signals[:self.crawl_limit])

    def _expand_list(self, hit, seen: set, position: int, ranked: list, query: str) -> None:
        """One fetch of a top-N page. The list host is not the company. No second try."""
        self.dropped["list_page"] += 1
        if self.http is None:
            return
        try:
            html = self.http.get(getattr(hit, "url", "") or "")
        except Exception:
            self.dropped["list_page_unreadable"] += 1
            return
        found = companies_from_list_html(html, getattr(hit, "url", "") or "", limit=list_company_limit())
        self.companies_from_lists += len(found)
        for name, url in found:
            signal = self._candidate(SearchHit(url=url, snippet=name, title=name), seen, position, company_name=name, found_query=query)
            if signal is not None:
                ranked.append(signal)

    def _candidate(self, hit, seen: set, position: int, company_name: str = "", found_query: str = "") -> tuple | None:
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
        if found_query and domain not in self.found_queries:
            self.found_queries[domain] = found_query
        folded = title.casefold()
        type_miss = 0 if any(term in folded for term in self.terms) else 1
        aggregator = 1 if _AGGREGATOR.search(title) else 0
        # The search hit is only how we found the domain. A signal date is read
        # later from the company's own page and is never this collection clock.
        signal = RawSignal(
            company_name=(company_name or domain)[:120], person_name="", site_url=root,
            signal_type="company_candidate", signal_date=datetime.now(timezone.utc),
            source_url=root, quote=snippet[:700], source=self.source_id, source_text=snippet,
            context_only=True, source_id=self.source_id)
        return ((aggregator, type_miss, position, domain), signal)
