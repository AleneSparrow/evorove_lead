"""Module 2: turn a `business_listing` hypothesis into a company list, not a person.

`hypothesis.py`'s `business_listing` kind already exists; what was missing
is the search step itself. Before this module, a business_listing query
went straight into the same one-hit-one-contact-attempt path as a person
search, so "auto repair shops near Austin" and "need catering near Austin"
were handled identically -- the first search hit on either just got probed
for an email.

`discover_companies` does one thing: run the hypothesis's query, keep only
results that look like a company's own site, and de-duplicate by domain so
a homepage and a services page from the same business only count once. The
result is a list of company candidates -- module 3's job is to visit each
one and find whoever decides, not the first address on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from evorove_lead.platforms import is_platform, registrable_domain
from evorove_lead.presence import PresenceRejected, validate_public_http_url
from evorove_lead.web_search import WebSearchClient

if TYPE_CHECKING:
    from evorove_lead.hypothesis import Hypothesis


@dataclass(frozen=True)
class CompanyCandidate:
    """One company's own site, found through a business_listing query.

    Not yet a person or a `Candidate` -- module 3 decides who at this
    company to contact.
    """

    url: str
    registrable_domain: str
    snippet: str
    query_used: str


def discover_companies(
    hypothesis: "Hypothesis", client: WebSearchClient
) -> tuple[CompanyCandidate, ...]:
    """Search once; keep the results that look like a company's own site.

    Filters out directories, forums, and social platforms -- the same
    `platforms.is_platform` check `web_people_search.py` uses to reject a
    contact found on a hosting site instead of the business's own domain --
    and any URL that fails the standard public-http(s) check. Two hits on
    the same registrable domain count once.
    """

    if hypothesis.intent_trigger.kind != "business_listing":
        raise ValueError("discover_companies is only for business_listing hypotheses")

    seen_domains: set[str] = set()
    companies: list[CompanyCandidate] = []
    for hit in client.search(hypothesis.query_template):
        try:
            validated = validate_public_http_url(hit.url)
        except PresenceRejected:
            continue
        host = urlsplit(validated).hostname or ""
        if is_platform(host):
            continue
        domain = registrable_domain(host)
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        companies.append(
            CompanyCandidate(
                url=validated,
                registrable_domain=domain,
                snippet=hit.snippet,
                query_used=hypothesis.query_template,
            )
        )
    return tuple(companies)
