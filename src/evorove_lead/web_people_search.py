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

# Deliberately strict: a plain 7-15 digit run (like observations.py's
# owner-deposited-JSONL parser uses) is far too noisy over a whole page --
# ZIP+4, prices, dates, and IDs all look like phone numbers under that
# rule. This requires actual phone punctuation (parens/space/dot/dash)
# between each group, which a "94105-1234" ZIP+4 or a "$1,234.5678"
# price does not have.
_STRICT_PHONE_RE = re.compile(r"(?<!\d)\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")


def _extract_phone(text: str) -> str | None:
    match = _STRICT_PHONE_RE.search(text)
    if match is None:
        return None
    digits = "".join(character for character in match.group(0) if character.isdigit())
    return digits if len(digits) == 10 else None


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

    Email is checked first and wins if both are on the page (email
    addressing this specific person is a stronger signal than a phone
    number that might be the business's own front desk). Phone only
    counts when it matches the strict, punctuated pattern in
    `_STRICT_PHONE_RE` -- a bare digit run is not enough.
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

        excerpt = (search_hit.snippet or page_text[:PAGE_TEXT_EXCERPT_CHARS]).strip()
        email_match = EMAIL_RE.search(page_text)
        if email_match is not None:
            hit = PeopleHit(
                identity=email_match.group(0).casefold(),
                observed_fact=excerpt,
                observed_source=search_hit.url,
                channel="email",
            )
            return TraceFinding(**{**base, "raw_text": excerpt}, hit=hit)

        phone = _extract_phone(page_text)
        if phone is not None:
            hit = PeopleHit(
                identity=phone,
                observed_fact=excerpt,
                observed_source=search_hit.url,
                channel="phone",  # never texted cold (TCPA)
            )
            return TraceFinding(**{**base, "raw_text": excerpt}, hit=hit)

        return TraceFinding(
            **{**base, "raw_text": excerpt},
            reject_reason="no email or phone found on the page",
        )
