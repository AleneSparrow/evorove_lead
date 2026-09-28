"""Shared "is this a company's own site, or someone else's platform?" check.

Split out of `web_people_search.py` so `business_listing.py` (module 2,
listing company candidates) and `web_people_search.py` (module 3's contact
extraction) can both use it without importing each other.
"""

from __future__ import annotations

# Step 19: a contact is a published *work* address of the company itself, or
# the person's own address in their post -- never the address of the page
# that happened to host it (a forum's admin@, a directory's info@).
PLATFORM_DOMAINS = frozenset(
    """
    reddit.com facebook.com instagram.com twitter.com x.com linkedin.com tiktok.com youtube.com
    nextdoor.com craigslist.org quora.com yelp.com medium.com wordpress.com blogspot.com
    yellowpages.com bbb.org angi.com angieslist.com thumbtack.com homeadvisor.com houzz.com
    google.com stackexchange.com stackoverflow.com patch.com city-data.com tripadvisor.com
    """.split()
)
PLATFORM_LABELS = ("forum", "forums", "community", "discuss", "board", "boards", "groups")


def registrable_domain(host: str) -> str:
    labels = [label for label in host.casefold().strip(".").split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.casefold()


def is_platform(host: str) -> bool:
    host = host.casefold()
    return registrable_domain(host) in PLATFORM_DOMAINS or any(
        label in PLATFORM_LABELS for label in host.split(".")[:-2]
    ) or any(part in host for part in ("forum", "community"))
