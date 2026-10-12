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

from evorove_lead.business_listing import CompanyCandidate
from evorove_lead.crm_touch import EMAIL_RE
from evorove_lead.decision_maker import fetch_candidate_pages, heuristic_decision_maker
from evorove_lead.decision_maker_llm import (
    DecisionMakerCompletion,
    decision_maker_completion_from_env,
    find_decision_maker_llm,
)
from evorove_lead.page_extract import relevant_fragment
from evorove_lead.platforms import PLATFORM_DOMAINS, is_platform, registrable_domain
from evorove_lead.prefilter import looks_like_spam
from evorove_lead.presence import PresenceRejected, validate_public_http_url
from evorove_lead.search import PeopleHit, TraceFinding
from evorove_lead.web_search import SearchHit, WebSearchClient, client_from_env, fetch_page_text

if TYPE_CHECKING:
    from evorove_lead.hypothesis import Hypothesis

_STRICT_PHONE_RE = re.compile(r"(?<!\d)\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
PAGE_TEXT_EXCERPT_CHARS = 280


def _extract_phone(text: str) -> str | None:
    match = _STRICT_PHONE_RE.search(text)
    if match is None:
        return None
    digits = "".join(character for character in match.group(0) if character.isdigit())
    return digits if len(digits) == 10 else None


# Step 19: a contact is a published *work* address of the company itself, or
# the person's own address in their post -- never the address of the page
# that happened to host it (a forum's admin@, a directory's info@).
# `is_platform`/`registrable_domain` moved to platforms.py so business_listing.py
# (module 2) can use the same check without importing this module back.
_OPS_LOCAL_PARTS = frozenset(
    """
    admin administrator webmaster postmaster hostmaster abuse noreply no-reply donotreply
    do-not-reply privacy legal dmca moderator mod root security mailer-daemon
    """.split()
)


def pick_contact_email(page_url: str, page_text: str, *, business: bool) -> str | None:
    """The one address that may become a candidate, or None."""

    host = urlsplit(page_url).hostname or ""
    page_domain = registrable_domain(host)
    for match in EMAIL_RE.finditer(page_text):
        address = match.group(0).casefold().strip(".")
        local, _, domain = address.partition("@")
        if local in _OPS_LOCAL_PARTS:
            continue
        if business:
            # The company's own site, and an address on the company's own domain.
            if not is_platform(host) and registrable_domain(domain) == page_domain:
                return address
            continue
        # A person's post: their own address, not the hosting site's.
        if registrable_domain(domain) != page_domain and registrable_domain(domain) not in PLATFORM_DOMAINS:
            return address
    return None


def _on_topic(hypothesis: "Hypothesis", snippet: str) -> bool:
    """Skip obvious spam. Missing keywords are not enough to drop a result.

    A paraphrase with none of the query's words still gets fetched so the
    later pre-filter and classifier can read it. Spam does not.
    """

    del hypothesis
    return not looks_like_spam(snippet)


def hypothesis_search_from_env() -> WebSearchPeopleSearch | None:
    """Live search is on only when the owner points `WEB_SEARCH_BASE_URL` at SearxNG.

    No URL means the engine stays unconnected. Do not pretend a harvest ran.
    """

    client = client_from_env()
    if client is None:
        return None
    return WebSearchPeopleSearch(client, decision_maker_llm=decision_maker_completion_from_env())


class WebSearchPeopleSearch:
    """Real connector: one search client, per-hypothesis queries, our own parsing.

    Email is checked first and wins if both are on the page (email
    addressing this specific person is a stronger signal than a phone
    number that might be the business's own front desk). Phone only
    counts when it matches the strict, punctuated pattern in
    `_STRICT_PHONE_RE` -- a bare digit run is not enough.
    """

    def __init__(
        self,
        client: WebSearchClient,
        page_fetcher=fetch_page_text,
        decision_maker_llm: DecisionMakerCompletion | None = None,
        page_extractor=None,
    ) -> None:
        self._client = client
        self._page_fetcher = page_fetcher
        self._decision_maker_llm = decision_maker_llm
        self._page_extractor = page_extractor
        self._extracted = {}
        self.connected = True

    def _fetch_text(self, url: str) -> str:
        if self._page_extractor is None:
            return self._page_fetcher(url)
        page = self._page_extractor(url)
        self._extracted[url] = page
        return page.text

    def find(self, hypothesis: "Hypothesis") -> Sequence[TraceFinding]:
        if hypothesis.intent_trigger.kind == "business_listing":
            return self._find_companies(hypothesis)
        return tuple(
            self._finding_for(hypothesis, search_hit)
            for search_hit in self._client.search(hypothesis.query_template)
        )

    def _find_companies(self, hypothesis: "Hypothesis") -> tuple[TraceFinding, ...]:
        """Module 2's list-then-contact split, one `TraceFinding` per raw hit.

        `business_listing.discover_companies` runs the same accept/reject
        rule as a standalone, directly testable "which companies did we
        find" step. This applies it hit by hit instead of delegating,
        because `TraceFinding` keeps every trace the warehouse should see,
        including the ones this rejects as a directory or a repeat of a
        company already found earlier in this same search -- silently
        dropping those would erase the audit trail `search.py` promises.
        """

        findings: list[TraceFinding] = []
        seen_domains: set[str] = set()
        for hit in self._client.search(hypothesis.query_template):
            base = dict(
                url=hit.url,
                raw_text=hit.snippet,
                query_used=hypothesis.query_template,
                source_channel=hypothesis.channel,
            )
            try:
                validated = validate_public_http_url(hit.url)
            except PresenceRejected as exc:
                findings.append(TraceFinding(**base, reject_reason=str(exc)))
                continue
            host = urlsplit(validated).hostname or ""
            if is_platform(host):
                findings.append(TraceFinding(**base, reject_reason="not the company's own website"))
                continue
            domain = registrable_domain(host)
            if domain in seen_domains:
                findings.append(
                    TraceFinding(**base, reject_reason="same company already found in this search")
                )
                continue
            seen_domains.add(domain)
            company = CompanyCandidate(
                url=validated, registrable_domain=domain, snippet=hit.snippet,
                query_used=hypothesis.query_template,
            )
            findings.append(self._finding_for_company(hypothesis, company))
        return tuple(findings)

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
            page_text = self._fetch_text(search_hit.url)
        except PresenceRejected as exc:
            return TraceFinding(**base, reject_reason=str(exc))

        excerpt = (search_hit.snippet or page_text[:PAGE_TEXT_EXCERPT_CHARS]).strip()
        return self._with_contact(base, excerpt, search_hit.url, page_text, source_kind="person")

    def _finding_for_company(self, hypothesis: "Hypothesis", company: "CompanyCandidate") -> TraceFinding:
        """Module 3: the named decision-maker first, any address on the
        domain as the fallback.

        `_find_companies` already dropped platforms, non-public URLs, and
        repeat domains before calling this. The homepage plus a few guessed
        About/Team paths are fetched once and tried against the regex
        heuristic for a name next to an owner/founder/director role word;
        when a `decision_maker_llm` is configured, the same already-fetched
        pages go to the LLM next if the heuristic found nothing. Only when
        neither finds anyone does this fall back to `pick_contact_email`'s
        wider "any address on the company's own domain" rule, which is how
        `info@` can still end up here.
        """

        base = dict(
            url=company.url,
            raw_text=company.snippet,
            query_used=hypothesis.query_template,
            source_channel=hypothesis.channel,
        )

        if not _on_topic(hypothesis, company.snippet):
            return TraceFinding(**base, reject_reason="trace is not about this hypothesis")

        pages = fetch_candidate_pages(company.url, self._fetch_text)
        decision_maker = heuristic_decision_maker(pages, company.registrable_domain)
        if decision_maker is None and self._decision_maker_llm is not None:
            decision_maker = find_decision_maker_llm(pages, company.registrable_domain, self._decision_maker_llm)
        if decision_maker is not None:
            hit = PeopleHit(
                identity=decision_maker.email,
                observed_fact=decision_maker.evidence_quote,
                observed_source=decision_maker.source_url,
                channel="email",
                source_kind="business",
            )
            return TraceFinding(
                **{**base, "raw_text": decision_maker.evidence_quote, "url": decision_maker.source_url},
                hit=hit,
            )

        # Anthropic is the reader when it is configured. A failed reading is
        # not replaced with a slice of the page or a generic inbox address.
        if self._decision_maker_llm is not None:
            return TraceFinding(**base, reject_reason="no grounded person on this page")

        homepage_text = dict(pages).get(company.url)
        if homepage_text is not None:
            page_text = homepage_text
        else:
            try:
                page_text = self._fetch_text(company.url)
            except PresenceRejected as exc:
                return TraceFinding(**base, reject_reason=str(exc))

        excerpt = (company.snippet or page_text[:PAGE_TEXT_EXCERPT_CHARS]).strip()
        return self._with_contact(base, excerpt, company.url, page_text, source_kind="business")

    def _with_contact(
        self, base: dict, excerpt: str, url: str, page_text: str, *, source_kind: str
    ) -> TraceFinding:
        email = pick_contact_email(url, page_text, business=source_kind == "business")
        meta = self._page_meta(url)
        if email is not None:
            hit = PeopleHit(
                identity=email,
                observed_fact=excerpt,
                observed_source=url,
                channel="email",
                source_kind=source_kind,
            )
            return TraceFinding(**{**base, "raw_text": excerpt}, hit=hit, page_text=relevant_fragment(page_text, base["query_used"]), **meta)

        phone = _extract_phone(page_text)
        if phone is not None:
            hit = PeopleHit(
                identity=phone,
                observed_fact=excerpt,
                observed_source=url,
                channel="phone",  # never texted cold (TCPA)
                source_kind=source_kind,
            )
            return TraceFinding(**{**base, "raw_text": excerpt}, hit=hit, page_text=relevant_fragment(page_text, base["query_used"]), **meta)

        return TraceFinding(
            **{**base, "raw_text": excerpt},
            reject_reason="no email or phone found on the page",
            page_text=relevant_fragment(page_text, base["query_used"]),
            **meta,
        )

    def _page_meta(self, url: str) -> dict[str, str]:
        page = self._extracted.get(url)
        if page is None:
            return {}
        return {
            "publication_date": page.publication_date or "",
            "page_author": page.author or "",
            "canonical_url": page.canonical_url or "",
        }
