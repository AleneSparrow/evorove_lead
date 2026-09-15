"""Which public channels to search on, given a business's own category.

`pattern_library.py` *learns* which `channel_family` closes well for an
archetype from measured outcomes -- but it starts every new archetype from
zero (`suggest_patterns` returns nothing until enough cases have closed).
Without this module every hypothesis defaults to plain `web_search`
forever, because nothing ever proposes a second channel to try.

This is static, hand-curated seed knowledge -- which platforms a given
*kind* of small business's customers publicly show up on. That is general
targeting knowledge a lead-gen consultant already has, not a fabricated
fact about any specific business; it never touches a business's own
materials or invents an audience. An archetype whose keywords match
nothing here gets no additional channel: `build_hypotheses` falls back to
`web_search` only. This module never guesses a platform for a business
type it doesn't recognize.

Every channel here still runs through the same self-hosted metasearch
endpoint (`web_search.py`) as a `site:`-restricted query -- not a new
scraper, API, or account. Adding a real platform-specific connector later
(an API with its own credentials) is a separate decision the owner makes;
this module only chooses which query to ask the existing search for.
"""

from __future__ import annotations

from dataclasses import dataclass

# Keyword -> category, matched case-insensitively as a substring of the
# free-text `business_archetype` (e.g. "bakery", "family law practice").
# Checked in this order; first category with a matching keyword wins.
# Deliberately narrow: a miss falls through to "" (no category), which
# `channels_for_archetype` treats as "add nothing", never a guess.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "local_service",
        (
            "salon", "barber", "spa", "nail", "tattoo", "massage", "clean",
            "plumb", "hvac", "electric", "contractor", "repair", "landscap",
            "gym", "fitness", "yoga", "studio", "dental", "dentist", "clinic",
            "vet", "groom",
        ),
    ),
    (
        "food_hospitality",
        (
            "bakery", "catering", "restaurant", "cafe", "coffee", "bar",
            "brewery", "chef", "food truck", "bistro",
        ),
    ),
    (
        "professional_services",
        (
            "law", "legal", "attorney", "lawyer", "accounting", "cpa", "tax",
            "financial", "insurance", "real estate", "realtor", "therapy",
            "counsel", "coach",
        ),
    ),
    (
        "b2b_software",
        (
            "software", "saas", "agency", "marketing", "consult",
            "it services", "automation", "platform", "app development",
        ),
    ),
)


@dataclass(frozen=True)
class ChannelTarget:
    """One platform to search on, expressed as a site-restricted web query.

    `channel` becomes the hypothesis's `channel` (and, downstream, a
    `TraceFinding.source_channel`) -- what shows up in the warehouse and
    later feeds `pattern_library`'s `channel_family`. `site_filter` is
    appended to the existing query template; it needs no new connector.
    """

    channel: str
    site_filter: str
    reason: str


_CHANNEL_LIBRARY: dict[str, tuple[ChannelTarget, ...]] = {
    "local_service": (
        ChannelTarget(
            "yelp", "site:yelp.com",
            "Local appointment businesses are found and reviewed on Yelp",
        ),
        ChannelTarget(
            "nextdoor", "site:nextdoor.com",
            "Neighbors ask for local service recommendations on Nextdoor",
        ),
        ChannelTarget(
            "instagram", "site:instagram.com",
            "Local service businesses and their customers are visually active on Instagram",
        ),
    ),
    "food_hospitality": (
        ChannelTarget(
            "instagram", "site:instagram.com",
            "Food and hospitality discovery is driven by Instagram",
        ),
        ChannelTarget(
            "yelp", "site:yelp.com",
            "Diners research and review food businesses on Yelp",
        ),
        ChannelTarget(
            "facebook", "site:facebook.com",
            "Local food businesses run community/event pages on Facebook",
        ),
    ),
    "professional_services": (
        ChannelTarget(
            "reddit", "site:reddit.com",
            "People publicly ask for professional-service recommendations on Reddit",
        ),
        ChannelTarget(
            "linkedin", "site:linkedin.com",
            "Professional-service buyers and referrals surface on LinkedIn",
        ),
    ),
    "b2b_software": (
        ChannelTarget(
            "linkedin", "site:linkedin.com",
            "B2B buyers and decision-makers are reachable through LinkedIn",
        ),
        ChannelTarget(
            "reddit", "site:reddit.com",
            "Niche B2B pain points surface in Reddit community discussions",
        ),
    ),
}


def categorize(business_archetype: str) -> str:
    """Best-effort category for a free-text archetype, or "" when nothing matches."""

    folded = business_archetype.casefold()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in folded for keyword in keywords):
            return category
    return ""


def channels_for_archetype(business_archetype: str) -> tuple[ChannelTarget, ...]:
    """Candidate extra channels for this archetype, or () when nothing is recognized.

    Empty is the honest default: an unrecognized or blank business type
    gets no additional platform, never a guessed one.
    """

    if not business_archetype.strip():
        return ()
    return _CHANNEL_LIBRARY.get(categorize(business_archetype), ())
