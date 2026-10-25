"""Public source catalog. The planner may select only enabled entries."""
from __future__ import annotations
from dataclasses import dataclass

CHECKED = "2026-10-25"
CATALOG_VERSION = "2026-10-25"


@dataclass(frozen=True)
class SourceSpec:
    id: str
    type: str
    description: str
    niche_tags: tuple[str, ...]
    provides: tuple[str, ...]
    automated_collection: bool
    terms_url: str
    terms_checked_at: str
    robots_url: str
    robots_note: str
    needs_login: bool
    needs_key: bool
    rate_limit: str
    status: str
    disabled_reason: str
    why_leads: str
    universal: bool = False
    requires_locality: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"enabled", "disabled"}:
            raise ValueError("source status")
        if self.status == "disabled" and not self.disabled_reason:
            raise ValueError("disabled source needs a reason")
        if self.status == "enabled" and not self.automated_collection:
            raise ValueError("enabled source must allow automatic collection")


def _spec(**kwargs) -> SourceSpec:
    kwargs.setdefault("terms_checked_at", CHECKED)
    return SourceSpec(**kwargs)


CATALOG: tuple[SourceSpec, ...] = (
    _spec(id="hn_show", type="api",
        description="Hacker News Show HN posts via the public Algolia API.",
        niche_tags=("hacker news", "show hn", "developer forum"),
        provides=("company", "site", "signal", "date"),
        automated_collection=True, terms_url="https://hn.algolia.com/api",
        robots_url="https://hn.algolia.com/robots.txt",
        robots_note="HTTP 404 on 2026-10-25, so there is no robots file. The API page returned 200 and stated no ban on this use.",
        needs_login=False, needs_key=False,
        rate_limit="At most 10 hits per collect and at least 1 second between requests to the host.",
        status="enabled", disabled_reason="",
        why_leads="Show HN is where developers post side projects. The post date is the signal. Author handles are not names, and the link is often a repository rather than a company."),
    _spec(id="hn_ask", type="api",
        description="Hacker News Ask HN posts via the public Algolia API.",
        niche_tags=("hacker news", "ask hn", "developer forum"),
        provides=("company", "site", "signal", "date"),
        automated_collection=True, terms_url="https://hn.algolia.com/api",
        robots_url="https://hn.algolia.com/robots.txt",
        robots_note="HTTP 404 on 2026-10-25, so there is no robots file. The API page returned 200 and stated no ban on this use.",
        needs_login=False, needs_key=False,
        rate_limit="At most 10 hits per collect and at least 1 second between requests to the host.",
        status="enabled", disabled_reason="",
        why_leads="Ask HN is a developer discussion thread. A post is a signal only when it links to a company site. The author handle is not a person's name."),
    _spec(id="hn_hiring", type="api",
        description="Comments on the current Ask HN: Who is hiring? thread via the public Algolia API.",
        niche_tags=("hacker news", "who is hiring", "developer forum"),
        provides=("company", "site", "signal", "date"),
        automated_collection=True, terms_url="https://hn.algolia.com/api",
        robots_url="https://hn.algolia.com/robots.txt",
        robots_note="HTTP 404 on 2026-10-25, so there is no robots file. The API page returned 200 and stated no ban on this use.",
        needs_login=False, needs_key=False,
        rate_limit="Two requests per collect: the monthly thread, then its comments. At least 1 second between requests to the host.",
        status="enabled", disabled_reason="",
        why_leads="The monthly Who is hiring thread is where engineering teams post jobs. It is not a directory of ordinary local businesses or online shops."),
    _spec(id="sec_form_d", type="filing",
        description="US SEC EDGAR Form D filings from the daily index under /Archives/edgar/data.",
        niche_tags=("form d", "securities", "capital raise", "issuer", "fundraise"),
        provides=("company", "person", "site", "signal", "date"),
        automated_collection=True, terms_url="https://www.sec.gov/os/accessing-edgar-data",
        robots_url="https://www.sec.gov/robots.txt",
        robots_note=("Checked 2026-10-25. www.sec.gov allows /Archives/edgar/data. Fair access allows a declared agent at or below 10 requests per second. "
            "efts.sec.gov/robots.txt returned 403, so that host is not called. The client identifies itself as EvoroveLead/0.1 (+https://evorove.com; public cycle-1 research); the index request returned 200. No email address was invented to match the sample header."),
        needs_login=False, needs_key=False,
        rate_limit="1 second between requests. At most 4 requests per collect: one daily index and up to 3 filings.",
        status="enabled", disabled_reason="",
        why_leads="Form D is a dated US filing by an issuer raising capital. It names the issuer. A company website is stored only when that filing text contains one. A personal email usually does not."),
    _spec(id="searxng_web", type="search",
        description="The owner's SearxNG instance, then a polite read of each resulting public company site.",
        niche_tags=(), provides=("company", "person", "site", "signal", "date", "contact"),
        automated_collection=True, terms_url="https://docs.searxng.org/",
        robots_url="",
        robots_note="The search base is the owner's own instance, including localhost. Every discovered page is fetched only through require_public_http_url and public_urlopen, after that site's robots.txt.",
        needs_login=False, needs_key=False,
        rate_limit="The existing SearxNG request cap, plus at most four pages per company site and at least 1 second per host.",
        status="enabled", disabled_reason="", universal=True,
        why_leads="Queries come from this client's own brief, so the same path works for any niche. The company site is then read for a person and a personal email. A platform host is not the company site."),
    _spec(id="osm_overpass", type="map",
        description="Local businesses from the public OpenStreetMap Overpass API.",
        niche_tags=("local business", "storefront"),
        provides=("company", "site", "signal", "date"),
        automated_collection=False, terms_url="https://overpass-api.de/robots.txt",
        robots_url="https://overpass-api.de/robots.txt",
        robots_note="Checked 2026-10-25: User-agent: * Disallow: /api/. The interpreter is under /api/.",
        needs_login=False, needs_key=False,
        rate_limit="Not collected.",
        status="disabled", requires_locality=True,
        disabled_reason=("overpass-api.de robots.txt checked 2026-10-25 disallows /api/ for every agent, and the public interpreter is under that path. "
            "The OpenStreetMap wiki and copyright pages did not connect (connection refused), so they were not treated as permission. Nominatim's usage policy was read and is a different service; it is not used."),
        why_leads="OpenStreetMap lists a local storefront with a name, a place, and sometimes a website. Personal emails are usually absent."),
    _spec(id="industry_rss", type="rss",
        description="RSS feeds of industry publications.",
        niche_tags=("industry publication", "trade press"),
        provides=("company", "site", "signal", "date"),
        automated_collection=False, terms_url="https://dev.to/terms",
        robots_url="https://www.saastr.com/robots.txt",
        robots_note="SaaStr /feed returned 200 and TechCrunch robots.txt does not disallow the feed path. A reachable feed is not a grant to reuse the articles.",
        needs_login=False, needs_key=False, rate_limit="Not collected.",
        status="disabled",
        disabled_reason=("No industry feed was cleared for automatic collection on 2026-10-25. DEV Community terms limit the materials to personal, non-commercial, transitory viewing. "
            "SaaStr's feed and TechCrunch's robots.txt were reachable, and neither publisher's terms were found to grant automated reuse of articles as a lead source."),
        why_leads="A trade publication can mention a company and date a story. The article page is not the company's site unless the item itself links to one."),
    _spec(id="yc_directory", type="directory",
        description="Y Combinator company directory and jobs.",
        niche_tags=("y combinator",), provides=("company", "person", "site", "signal", "date"),
        automated_collection=False, terms_url="https://www.ycombinator.com/legal",
        robots_url="https://www.ycombinator.com/robots.txt",
        robots_note="Re-read 2026-10-25: Disallow: /companies?*.",
        needs_login=False, needs_key=False, rate_limit="Not collected.",
        status="disabled",
        disabled_reason="Y Combinator Terms of Use prohibit scraping, data mining, and robots. robots.txt re-read on 2026-10-25 disallows /companies?*.",
        why_leads="The directory lists funded companies, founders, and jobs. Automatic collection would break the terms."),
    _spec(id="indie_hackers", type="community",
        description="Indie Hackers posts.",
        niche_tags=("indie hackers",), provides=("person", "signal", "date"),
        automated_collection=False, terms_url="https://www.indiehackers.com/terms",
        robots_url="https://www.indiehackers.com/robots.txt",
        robots_note="Terms re-read 2026-10-25. The terms ban is the reason, not a robots line.",
        needs_login=False, needs_key=False, rate_limit="Not collected.",
        status="disabled",
        disabled_reason="Indie Hackers terms, re-read 2026-10-25, prohibit crawling or scraping the service and copying a significant portion of the content.",
        why_leads="Founders write there under their own names. The terms do not allow a parser."),
    _spec(id="reddit", type="community",
        description="Reddit posts.",
        niche_tags=("reddit",), provides=("signal", "date"),
        automated_collection=False, terms_url="https://www.reddit.com/robots.txt",
        robots_url="https://www.reddit.com/robots.txt",
        robots_note="Checked 2026-10-25: User-agent: * Disallow: /.",
        needs_login=True, needs_key=True, rate_limit="Not collected.",
        status="disabled",
        disabled_reason="Reddit robots.txt checked 2026-10-25 says User-agent: * Disallow: /. A login or API key would be required, and there is no bypass.",
        why_leads="Subreddits can contain a founder's own words. They are not readable as an anonymous public fetch."),
    _spec(id="product_hunt", type="directory",
        description="Product Hunt launches.",
        niche_tags=("product hunt",), provides=("company", "site", "signal", "date"),
        automated_collection=False, terms_url="https://www.producthunt.com/robots.txt",
        robots_url="https://www.producthunt.com/robots.txt",
        robots_note="Checked 2026-10-25: HTTP 403 and a challenge page, so robots.txt could not be parsed.",
        needs_login=True, needs_key=True, rate_limit="Not collected.",
        status="disabled",
        disabled_reason="Product Hunt robots.txt returned 403 on 2026-10-25 behind a challenge page. No keyless machine-readable access was established.",
        why_leads="Launches can name a product and a site. The site did not allow an anonymous fetch."),
    _spec(id="dev_to", type="community",
        description="DEV Community articles.",
        niche_tags=("dev community",), provides=("person", "signal", "date"),
        automated_collection=False, terms_url="https://dev.to/terms",
        robots_url="https://dev.to/robots.txt",
        robots_note="Terms re-read 2026-10-25. The license, not a robots line, is the reason.",
        needs_login=False, needs_key=False, rate_limit="Not collected.",
        status="disabled",
        disabled_reason="DEV Community terms, re-read 2026-10-25, grant personal, non-commercial, transitory viewing only.",
        why_leads="Developers publish there under their names. The license does not allow this commercial use."),
)


def source_by_id(source_id: str) -> SourceSpec | None:
    return next((item for item in CATALOG if item.id == source_id), None)


def enabled_sources() -> tuple[SourceSpec, ...]:
    return tuple(item for item in CATALOG if item.status == "enabled")
