"""The first live `HypothesisPeopleSearch` connector: our own fetch and parse.

Not LinkedIn, not a paywall, not a purchased list, and not a paid
third-party search API -- a self-hosted metasearch endpoint (`web_search.py`)
for finding candidate URLs, plus our own page fetch and extraction. Every
result becomes a `TraceFinding`; a trace only carries a `PeopleHit` when an
email address actually turned up on the page.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Sequence

from evorove_lead.crm_touch import EMAIL_RE
from evorove_lead.presence import PresenceRejected, validate_public_http_url
from evorove_lead.search import PeopleHit, TraceFinding
from evorove_lead.web_search import SearchHit, WebSearchClient, fetch_page_text

if TYPE_CHECKING:
    from evorove_lead.hypothesis import Hypothesis

_KEYWORD_RE = re.compile(r"[A-Za-z]{4,}")
_STOPWORDS = frozenset({"near", "need", "looking", "for", "with"})
PAGE_TEXT_EXCERPT_CHARS = 280


def _keywords(query_template: str) -> tuple[str, ...]:
    words = {w.casefold() for w in _KEYWORD_RE.findall(query_template)}
    return tuple(words - _STOPWORDS)


def _on_topic(hypothesis: "Hypothesis", snippet: str) -> bool:
    """Heuristic topic check: does anything from the query show up in the result?

    Lightweight by design (contract calls this an "анализ следа" gate, not
    full re-analysis) -- `candidate.py` still does the real offer-fit check
    once a person is extracted.
    """

    keywords = _keywords(hypothesis.query_template)
    if not keywords:
        return True
    folded = snippet.casefold()
    return any(word in folded for word in keywords)


class WebSearchPeopleSearch:
    """Real connector: one search client, per-hypothesis queries, our own parsing.

    Phone extraction from raw page bodies is deliberately not attempted --
    scanning a whole page for digit runs is too noisy (dates, prices, zip
    codes all look like phone numbers). Only an email counts as an address
    for this first connector; a stricter phone pattern is future work.
    """

    def __init__(self, client: WebSearchClient, page_fetcher=fetch_page_text) -> None:
        self._client = client
        self._page_fetcher = page_fetcher
        self.connected = True

    def find(self, hypothesis: "Hypothesis") -> Sequence[TraceFinding]:
        return tuple(
            self._finding_for(hypothesis, search_hit)
            for search_hit in self._client.search(hypothesis.query_template)
        )

    def _finding_for(self, hypothesis: "Hypothesis", search_hit: SearchHit) -> TraceFinding:
        base = dict(
            url=search_hit.url,
            raw_text=search_hit.snippet,
            query_used=hypothesis.query_template,
            source_channel=hypothesis.channel,
        )

        if not _on_topic(hypothesis, search_hit.snippet):
            return TraceFinding(**base, reject_reason="trace is not about this hypothesis")

        try:
            validate_public_http_url(search_hit.url)
        except PresenceRejected as exc:
            return TraceFinding(**base, reject_reason=str(exc))

        try:
            page_text = self._page_fetcher(search_hit.url)
        except PresenceRejected as exc:
            return TraceFinding(**base, reject_reason=str(exc))

        match = EMAIL_RE.search(page_text)
        if match is None:
            return TraceFinding(
                **{**base, "raw_text": search_hit.snippet or page_text[:PAGE_TEXT_EXCERPT_CHARS]},
                reject_reason="no email address found on the page",
            )

        email = match.group(0).casefold()
        excerpt = (search_hit.snippet or page_text[:PAGE_TEXT_EXCERPT_CHARS]).strip()
        hit = PeopleHit(
            identity=email,
            observed_fact=excerpt,
            observed_source=search_hit.url,
            channel="email",
        )
        return TraceFinding(**{**base, "raw_text": excerpt}, hit=hit)
