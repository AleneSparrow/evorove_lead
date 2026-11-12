"""Public job boards for client 0 when SearxNG returns nothing.

Discovery is the keyless HN Algolia API. Postings are read only from the
keyless Greenhouse, Lever, and Ashby JSON APIs. Company contact pages are
fetched only at /contact and /about. No HTML board scraping, no login, no
address guessing, no people-search service.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from evorove_lead.candidate import accept_candidate
from evorove_lead.crm_touch import assembled_touch, sink_from_env
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.observed_pain import hiring_post_pain
from evorove_lead.offer import OfferUnderstanding
from evorove_lead.presence import USER_AGENT
from evorove_lead.warehouse import CandidateRecord, new_id
from evorove_lead.web_search import fetch_page_text

_ALGOLIA = "https://hn.algolia.com/api/v1"
_API_HOSTS = frozenset({
    "hn.algolia.com",
    "boards-api.greenhouse.io",
    "api.lever.co",
    "api.ashbyhq.com",
})
_SKIP_HOSTS = frozenset({
    "news.ycombinator.com",
    "hn.algolia.com",
    "jobs.ashbyhq.com",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards-api.greenhouse.io",
    "jobs.lever.co",
    "api.lever.co",
    "api.ashbyhq.com",
    "linkedin.com",
    "www.linkedin.com",
})
_FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "icloud.com", "proton.me", "protonmail.com", "aol.com", "live.com",
})
_ROLE_LOCAL = frozenset({
    "info", "hello", "contact", "sales", "team", "office", "careers", "jobs",
    "partnerships", "inquiries", "hi",
})
_STATES = frozenset(
    "AL AK AZ AR CA CO CT DC DE FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN "
    "MO MS MT NC ND NE NH NJ NM NV NY OH OK OR PA RI SC SD TN TX UT VA VT WA "
    "WI WV WY".split()
)
_BOARD = re.compile(
    r"https?://(?:jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)"
    r"|(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9._-]+)"
    r"|jobs\.lever\.co/([A-Za-z0-9._-]+))",
    re.I,
)
_KIND = ("ashby", "greenhouse", "lever")
_SALES = re.compile(
    r"\b(?:account executives?|sales(?:person|people)?|sdr|bdr|business development)\b",
    re.I,
)
_MARKER = re.compile(
    r"\b(?:founding|first sales hire|first account executive|founder-led|no sales team)\b",
    re.I,
)
_US = re.compile(r"\b(?:united states|u\.s\.a?\.?|usa|us)\b", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SENTENCE = re.compile(r"[A-Z][\x20-\x7e]+[.!?]")
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r"https?://[^\s<>\"']+", re.I)


@dataclass(frozen=True)
class Board:
    kind: str
    token: str


@dataclass(frozen=True)
class Posting:
    company: str
    title: str
    url: str
    location: str
    text: str
    homepage: str = ""


@dataclass(frozen=True)
class HiringLead:
    company: str
    posting_url: str
    text: str
    reason: str
    email: str = ""
    homepage: str = ""


@dataclass
class HiringReport:
    threads_read: int = 0
    boards_found: int = 0
    postings_read: int = 0
    requests: int = 0
    boards_skipped: int = 0
    leads: tuple[HiringLead, ...] = ()

    def as_dict(self) -> dict:
        return {
            "threads_read": self.threads_read,
            "boards_found": self.boards_found,
            "boards_skipped": self.boards_skipped,
            "postings_read": self.postings_read,
            "requests": self.requests,
        }


class HiringFetchError(RuntimeError):
    pass


def _plain(value: str) -> str:
    text = html.unescape(_TAG.sub(" ", value or ""))
    return " ".join(text.split())


def board_links(text: str) -> tuple[Board, ...]:
    found: list[Board] = []
    seen: set[tuple[str, str]] = set()
    for match in _BOARD.finditer(html.unescape(text or "")):
        for kind, token in zip(_KIND, match.groups()):
            if not token:
                continue
            token = token.strip(".").strip()
            key = (kind, token.casefold())
            if key in seen:
                continue
            seen.add(key)
            found.append(Board(kind, token))
    return tuple(found)


def board_api_url(board: Board) -> str:
    if board.kind == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board.token}/jobs?content=true"
    if board.kind == "lever":
        return f"https://api.lever.co/v0/postings/{board.token}?mode=json"
    if board.kind == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{board.token}"
    raise HiringFetchError("unknown board")


def is_us_or_us_remote(location: str) -> bool:
    text = location or ""
    if _US.search(text):
        return True
    tokens = {part.strip(".,()").upper() for part in re.split(r"[\s/|-]+", text) if part.strip(".,()")}
    return bool(tokens & _STATES)


def keep_posting(title: str, location: str, text: str) -> bool:
    blob = f"{title}\n{text}"
    return bool(_SALES.search(title) and _MARKER.search(blob) and is_us_or_us_remote(location))


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).strip() for match in _SENTENCE.finditer(text or ""))


_PROCESS = re.compile(r"\b(?:no sales process|without a sales process)\b", re.I)


def marker_sentence(text: str) -> str:
    for sentence in _sentences(text):
        if _MARKER.search(sentence) or _PROCESS.search(sentence):
            return sentence
    return ""


def published_business_email(text: str, *, site_host: str = "") -> str:
    """A role address the company published. Never a guessed pattern or a personal mailbox."""
    host = (site_host or "").casefold().removeprefix("www.")
    for match in _EMAIL.finditer(text or ""):
        address = match.group(0).casefold().strip(".")
        local, _, domain = address.partition("@")
        domain = domain.removeprefix("www.")
        if "." in local or local not in _ROLE_LOCAL:
            continue
        if domain in _FREE_MAIL or domain in _SKIP_HOSTS:
            continue
        if host and domain != host and not domain.endswith("." + host):
            continue
        return address
    return ""


def _site_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host.removeprefix("www.")


def homepage_in(text: str) -> str:
    for match in _HREF.finditer(html.unescape(text or "")):
        url = match.group(0).rstrip(").,]}>\"'")
        host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
        if not host or host in _SKIP_HOSTS or host.endswith(".greenhouse.io") or host.endswith(".lever.co") or host.endswith(".ashbyhq.com"):
            continue
        return url
    return ""


def parse_hn_stories(payload: dict) -> tuple[dict, ...]:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    rows = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        title = str(hit.get("title") or "")
        if not title.startswith("Ask HN: Who is hiring?"):
            continue
        object_id = str(hit.get("objectID") or "").strip()
        if object_id:
            rows.append({"id": object_id, "title": title})
    return tuple(rows)


def comment_texts(node) -> tuple[str, ...]:
    """Every comment in an Algolia item tree. The sales filter is not applied here."""
    found: list[str] = []

    def walk(item) -> None:
        if not isinstance(item, dict):
            return
        text = item.get("text") or item.get("comment_text") or ""
        if text:
            found.append(str(text))
        for child in item.get("children") or []:
            walk(child)

    walk(node)
    return tuple(found)


def _location_of(item: dict, kind: str) -> str:
    if kind == "greenhouse":
        location = item.get("location") or {}
        return str(location.get("name") or "") if isinstance(location, dict) else ""
    if kind == "lever":
        categories = item.get("categories") or {}
        if isinstance(categories, dict):
            return " ".join(str(categories.get(key) or "") for key in ("location", "commitment"))
        return ""
    location = str(item.get("location") or "")
    address = item.get("address") or {}
    if isinstance(address, dict):
        postal = address.get("postalAddress") or {}
        if isinstance(postal, dict):
            location = " ".join(part for part in (location, str(postal.get("addressCountry") or "")) if part)
    if item.get("isRemote") and "remote" not in location.casefold():
        location = f"Remote {location}".strip()
    return location


def _body_of(item: dict, kind: str) -> str:
    if kind == "greenhouse":
        return _plain(str(item.get("content") or ""))
    if kind == "lever":
        return _plain(str(item.get("descriptionPlain") or item.get("description") or ""))
    return _plain(str(item.get("descriptionPlain") or item.get("descriptionHtml") or ""))


def parse_postings(kind: str, token: str, payload) -> tuple[Posting, ...]:
    if kind == "lever":
        jobs = payload if isinstance(payload, list) else []
    else:
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        jobs = jobs or []
    company = token.replace("-", " ").replace("_", " ").strip()
    rows = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("text") or "")
        url = str(item.get("absolute_url") or item.get("hostedUrl") or item.get("jobUrl") or "")
        rows.append(Posting(
            company=company,
            title=title,
            url=url,
            location=_location_of(item, kind),
            text=_body_of(item, kind),
        ))
    return tuple(rows)


def _hn_url(path: str, params: dict) -> str:
    return f"{_ALGOLIA}/{path}?{urlencode(params)}"


class JsonClient:
    """One cache for the run. A repeated URL does not spend another request."""

    def __init__(self, opener: Callable[[str], bytes], *, pause: float = 0.0, limit: int = 40):
        self._opener = opener
        self._pause = max(0.0, pause)
        self._limit = limit
        self._cache: dict[str, object] = {}
        self.requests = 0

    def get(self, url: str):
        if url in self._cache:
            return self._cache[url]
        if self.requests >= self._limit:
            raise HiringFetchError("hiring_request_limit")
        if self.requests and self._pause:
            time.sleep(self._pause)
        self.requests += 1
        host = (urlsplit(url).hostname or "").casefold()
        if host not in _API_HOSTS:
            raise HiringFetchError("hiring_host_not_allowed")
        try:
            body = self._opener(url)
            data = json.loads(body.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeError) as exc:
            raise HiringFetchError("hiring_fetch_failed") from exc
        self._cache[url] = data
        return data


def live_opener(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    chunks = []
    total = 0
    cap = 8_000_000
    with urlopen(request, timeout=30) as response:
        while True:
            block = response.read(65536)
            if not block:
                break
            total += len(block)
            if total > cap:
                raise OSError("hiring response too large")
            chunks.append(block)
    return b"".join(chunks)


def collect_hiring(
    client: JsonClient,
    *,
    fetch_text: Callable[[str], str] | None = None,
    threads: int = 6,
) -> HiringReport:
    report = HiringReport()
    per_page = 30
    stories: list[dict] = []
    seen_stories: set[str] = set()
    for page in range(4):
        if len(stories) >= threads:
            break
        stories_url = _hn_url("search_by_date", {
            "tags": "story",
            "hitsPerPage": per_page,
            "page": page,
            "query": "Ask HN: Who is hiring?",
        })
        try:
            payload = client.get(stories_url)
        except HiringFetchError:
            break
        hits = payload.get("hits") if isinstance(payload, dict) else None
        for story in parse_hn_stories(payload if isinstance(payload, dict) else {}):
            if story["id"] in seen_stories:
                continue
            seen_stories.add(story["id"])
            stories.append(story)
            if len(stories) >= threads:
                break
        if not isinstance(hits, list) or len(hits) < per_page:
            break
    comments: list[str] = []
    for story in stories[:threads]:
        try:
            comments.extend(comment_texts(client.get(f"{_ALGOLIA}/items/{story['id']}")))
        except HiringFetchError:
            continue
        report.threads_read += 1
    boards: list[Board] = []
    seen_boards: set[tuple[str, str]] = set()
    homes: dict[tuple[str, str], str] = {}
    notes: dict[tuple[str, str], str] = {}
    for text in comments:
        page = homepage_in(text)
        for board in board_links(text):
            key = (board.kind, board.token.casefold())
            notes[key] = f"{notes.get(key, '')}\n{text}"
            if page and key not in homes:
                homes[key] = page
            if key in seen_boards:
                continue
            seen_boards.add(key)
            boards.append(board)
    report.boards_found = len(boards)
    leads: list[HiringLead] = []
    seen_urls: set[str] = set()
    contact_fetches = 0
    for index, board in enumerate(boards):
        try:
            payload = client.get(board_api_url(board))
        except HiringFetchError as exc:
            if str(exc) == "hiring_request_limit":
                report.boards_skipped = len(boards) - index
                break
            continue
        postings = parse_postings(board.kind, board.token, payload)
        report.postings_read += len(postings)
        note = notes.get((board.kind, board.token.casefold()), "")
        homepage = homes.get((board.kind, board.token.casefold()), "")
        for posting in postings:
            if not posting.url or posting.url in seen_urls:
                continue
            if not keep_posting(posting.title, posting.location, posting.text):
                continue
            seen_urls.add(posting.url)
            email = published_business_email(note)
            if not email and homepage and fetch_text is not None and contact_fetches < 8:
                contact_fetches += 1
                if client._pause:
                    time.sleep(client._pause)
                email = _email_from_site(homepage, fetch_text)
            reason = marker_sentence(posting.text) or posting.title.strip()
            leads.append(HiringLead(
                company=posting.company,
                posting_url=posting.url,
                text=posting.text,
                reason=reason,
                email=email,
                homepage=homepage,
            ))
    report.leads = tuple(leads)
    report.requests = client.requests
    return report


def _email_from_site(homepage: str, fetch_text: Callable[[str], str]) -> str:
    parts = urlsplit(homepage)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    origin = f"{parts.scheme}://{parts.netloc}"
    host = _site_host(origin)
    for path in ("/contact", "/about"):
        try:
            text = fetch_text(origin + path)
        except (OSError, ValueError, TimeoutError):
            continue
        email = published_business_email(text, site_host=host)
        if email:
            return email
    return ""


def place_hiring_leads(
    leads: tuple[HiringLead, ...],
    offer: OfferUnderstanding,
    *,
    business_id: str,
    dry_run: bool,
    warehouse=None,
    sink=None,
) -> dict:
    """Cold cards. A published role address can go to cycle 2. No address stays needs_contact."""
    from evorove_lead.sqlalchemy_warehouse import warehouse_from_env

    warehouse = warehouse if warehouse is not None else warehouse_from_env()
    sink = sink if sink is not None else sink_from_env()
    accepted = []
    handoffs = 0
    with_contact = 0
    for lead in leads:
        identity = f"{lead.company} {lead.email}".strip() if lead.email else lead.company
        if not identity or not lead.reason or not lead.posting_url:
            continue
        candidate = accept_candidate(identity=identity, reason=lead.reason, reason_source=lead.posting_url)
        pain, excerpt = hiring_post_pain(lead.text, offer)
        if pain:
            candidate = replace(
                candidate, observed_pain=pain, pain_evidence_excerpt=excerpt,
                pain_evidence_source=lead.posting_url,
            )
        accepted.append(candidate)
        emailed = bool(lead.email)
        if emailed:
            with_contact += 1
        if business_id:
            record = CandidateRecord(
                id=new_id("candidate"),
                business_id=business_id,
                hypothesis_id="hiring-boards",
                identity=candidate.identity,
                channel="email" if emailed else "unknown",
                reason=candidate.reason,
                reason_source=candidate.reason_source,
                fit=1.0,
                evidence=1.0,
                addressable=emailed,
                decision="cold" if emailed else "needs_contact",
                decided_at=datetime.now(timezone.utc),
                email=lead.email,
                observed_pain=candidate.observed_pain,
                pain_evidence_excerpt=candidate.pain_evidence_excerpt,
                pain_evidence_source=candidate.pain_evidence_source,
            )
            warehouse.save_candidate(record)
        if emailed and business_id and not dry_run:
            handoff = Cycle1Handoff.from_candidate(candidate, "email", hypothesis_id="hiring-boards")
            sink.publish(business_id, assembled_touch(business_id, candidate, handoff))
            handoffs += 1
    from evorove_lead.observed_pain import pain_counts
    counts = pain_counts(accepted)
    return {
        "candidates": accepted,
        "handoffs": handoffs,
        "with_contact": with_contact,
        "without_contact": len(accepted) - with_contact,
        **counts,
    }


def live_source(*, pause: float | None = None, limit: int | None = None) -> JsonClient:
    if pause is None:
        try:
            pause = float(os.getenv("HIRING_PAUSE_SECONDS", "0.4"))
        except ValueError:
            pause = 0.4
    if limit is None:
        try:
            limit = int(os.getenv("HIRING_MAX_REQUESTS", "40"))
        except ValueError:
            limit = 40
    return JsonClient(live_opener, pause=pause, limit=limit)
