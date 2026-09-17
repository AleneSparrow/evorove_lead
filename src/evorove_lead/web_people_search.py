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
from urllib.parse import urlsplit

from evorove_lead.crm_touch import EMAIL_RE
from evorove_lead.presence import PresenceRejected, validate_public_http_url
from evorove_lead.search import PeopleHit, TraceFinding
from evorove_lead.web_search import SearchHit, WebSearchClient, client_from_env, fetch_page_text

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


# Step 19: a contact is a published *work* address of the company itself, or
# the person's own address in their post -- never the address of the page
# that happened to host it (a forum's admin@, a directory's info@).
_PLATFORM_DOMAINS = frozenset(
    """
    reddit.com facebook.com instagram.com twitter.com x.com linkedin.com tiktok.com youtube.com
    nextdoor.com craigslist.org quora.com yelp.com medium.com wordpress.com blogspot.com
    yellowpages.com bbb.org angi.com angieslist.com thumbtack.com homeadvisor.com houzz.com
    google.com stackexchange.com stackoverflow.com patch.com city-data.com tripadvisor.com
    """.split()
)
_PLATFORM_LABELS = ("forum", "forums", "community", "discuss", "board", "boards", "groups")
_OPS_LOCAL_PARTS = frozenset(
    """
    admin administrator webmaster postmaster hostmaster abuse noreply no-reply donotreply
    do-not-reply privacy legal dmca moderator mod root security mailer-daemon
    """.split()
)


def _registrable_domain(host: str) -> str:
    labels = [label for label in host.casefold().strip(".").split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.casefold()


def _is_platform(host: str) -> bool:
    host = host.casefold()
    return _registrable_domain(host) in _PLATFORM_DOMAINS or any(
        label in _PLATFORM_LABELS for label in host.split(".")[:-2]
    ) or any(part in host for part in ("forum", "community"))


def pick_contact_email(page_url: str, page_text: str, *, business: bool) -> str | None:
    """The one address that may become a candidate, or None."""

    host = urlsplit(page_url).hostname or ""
    page_domain = _registrable_domain(host)
    for match in EMAIL_RE.finditer(page_text):
        address = match.group(0).casefold().strip(".")
        local, _, domain = address.partition("@")
        if local in _OPS_LOCAL_PARTS:
            continue
        if business:
            # The company's own site, and an address on the company's own domain.
            if not _is_platform(host) and _registrable_domain(domain) == page_domain:
                return address
            continue
        # A person's post: their own address, not the hosting site's.
        if _registrable_domain(domain) != page_domain and _registrable_domain(domain) not in _PLATFORM_DOMAINS:
            return address
    return None


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


def hypothesis_search_from_env() -> WebSearchPeopleSearch | None:
    """Live search is on only when the owner points `WEB_SEARCH_BASE_URL` at SearxNG.

    No URL means the engine stays unconnected. Do not pretend a harvest ran.
    """

    client = client_from_env()
    if client is None:
        return None
    return WebSearchPeopleSearch(client)


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
        business = hypothesis.intent_trigger.kind == "business_listing"
        source_kind = "business" if business else "person"
        if business and _is_platform(urlsplit(search_hit.url).hostname or ""):
            return TraceFinding(**{**base, "raw_text": excerpt}, reject_reason="not the company's own website")
        email = pick_contact_email(search_hit.url, page_text, business=business)
        if email is not None:
            hit = PeopleHit(
                identity=email,
                observed_fact=excerpt,
                observed_source=search_hit.url,
                channel="email",
                source_kind=source_kind,
            )
            return TraceFinding(**{**base, "raw_text": excerpt}, hit=hit)

        phone = _extract_phone(page_text)
        if phone is not None:
            hit = PeopleHit(
                identity=phone,
                observed_fact=excerpt,
                observed_source=search_hit.url,
                channel="phone",  # never texted cold (TCPA)
                source_kind=source_kind,
            )
            return TraceFinding(**{**base, "raw_text": excerpt}, hit=hit)

        return TraceFinding(
            **{**base, "raw_text": excerpt},
            reject_reason="no email or phone found on the page",
        )
