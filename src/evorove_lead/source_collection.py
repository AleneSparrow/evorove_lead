"""Collect public signals and each candidate site once per TTL; no LLM."""
from __future__ import annotations
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from evorove_lead.company_facts import parse_page_date
from evorove_lead.competitor_offers import competitor_offer, offer_surface
from evorove_lead.decision_maker import find_named_leader, heuristic_decision_maker
from evorove_lead.page_extract import extract_html, content_sha256
from evorove_lead.platforms import registrable_domain, is_platform
from evorove_lead.presence import require_public_http_url
from evorove_lead.source_adapters import RawSignal, SourceAdapter, PublicSourceHTTP
from evorove_lead.source_core_models import SourceCompany, SourcePerson, SourceContact, SourceRawPage, SourceSignal

_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_GENERIC = {'info','admin','support','hello','contact','sales','office','team','noreply'}
_HREF = re.compile(r"""href=["']([^"'#]+)["']""", re.I)
_PAGE_PATHS = ("careers", "jobs", "blog", "news", "case-studies", "cases", "pricing", "about", "team", "leadership", "contact", "clients", "work")
_PAGE_LIMIT = 4
_CHALLENGE = ("client challenge", "just a moment", "cf-browser-verification", "attention required", "captcha")

def company_page_urls(root: str, homepage_html: str, *, limit: int = _PAGE_LIMIT) -> tuple[str, ...]:
    """Root plus up to three same-site pages the homepage actually links. Never more than `limit` fetches."""
    root = root if root.endswith("/") else root + "/"
    found = []
    for match in _HREF.finditer(homepage_html or ""):
        absolute = urljoin(root, match.group(1).strip())
        parts = urlsplit(absolute)
        if f"{parts.scheme}://{parts.netloc}/" != root and parts.netloc != urlsplit(root).netloc:
            continue
        path = parts.path.casefold()
        if not any(re.search(rf"(?:^|/){re.escape(name)}(?:/|$)", path) for name in _PAGE_PATHS):
            continue
        cleaned = f"{parts.scheme}://{parts.netloc}{parts.path}"
        if cleaned.rstrip("/") == root.rstrip("/") or cleaned in found:
            continue
        found.append(cleaned)
    found.sort(key=lambda url: next((i for i, name in enumerate(_PAGE_PATHS) if re.search(rf"(?:^|/){re.escape(name)}(?:/|$)", urlsplit(url).path.casefold())), len(_PAGE_PATHS)))
    return (root, *found[:limit - 1])

def classify_fetch_error(exc: BaseException) -> str:
    """Why a page was not read. This does not retry and does not solve a challenge."""
    if isinstance(exc, HTTPError):
        return "blocked_403" if exc.code == 403 else "blocked_http"
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, (RemoteDisconnected, ConnectionResetError, TimeoutError, ConnectionError, BrokenPipeError)):
        return "blocked_connection"
    text = str(exc).casefold()
    if "403" in text:
        return "blocked_403"
    if "closed" in text or "disconnect" in text or "connection" in text or "timed out" in text:
        return "blocked_connection"
    return "blocked_fetch"


def is_challenge_page(title: str, text: str) -> bool:
    blob = f"{title or ''}\n{(text or '')[:500]}".casefold()
    return any(mark in blob for mark in _CHALLENGE)


def public_mx_exists(domain: str) -> bool:
    """DNS check during collection; fail closed on timeout/NXDOMAIN/null MX."""
    import dns.resolver
    try:
        answers = dns.resolver.resolve(domain, 'MX', lifetime=3)
        return any(str(answer.exchange).rstrip('.') for answer in answers)
    except Exception:
        return False

_CONTACT_FIELD = re.compile(r"email|e-mail|message|phone|tel|name|company|comment", re.I)
_SEARCH_FIELD = re.compile(r"^(?:q|s|query|search|keyword|submit)$", re.I)


def contact_form_target(forms, page_url: str) -> tuple[str, str]:
    """URL and field names of a contact form. A search box is not a contact path."""
    for action, fields in forms or ():
        names = [field for field in fields if field and not _SEARCH_FIELD.match(field)]
        if not names or not any(_CONTACT_FIELD.search(field) for field in names):
            continue
        return urljoin(page_url, action or page_url), "|".join(names)
    return "", ""


def _stored_text(page, page_url: str) -> str:
    text = _with_country(page.text, page.address_country)
    target, names = contact_form_target(page.forms, page_url)
    if not target:
        return text
    return f"{text} contact_form {target} fields {names}"


def _visible_email(pages, domain: str, name: str) -> str:
    """An address written on the page. Nothing is invented from the name."""
    words = [word.casefold() for word in (name or "").split() if word]
    first = words[0] if words else ""
    last = words[-1] if words else ""
    fallback = ""
    for item in pages:
        emails = item[3] if len(item) > 3 else ()
        for raw in (*emails, *_EMAIL.findall(item[1] or "")):
            address = raw.strip().strip(".").casefold()
            if not _EMAIL.fullmatch(address):
                continue
            local, _, host = address.partition("@")
            if registrable_domain(host) != domain:
                continue
            if first and (first in local or last in local):
                return address
            if not fallback:
                fallback = address
    return fallback


def _with_country(text: str, country: str) -> str:
    country = (country or "").strip()
    if not country:
        return text
    return f"{text} addressCountry {country}."


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class SourceCollector:
    def __init__(self, engine, http: PublicSourceHTTP, *, mx_check=public_mx_exists, clock=lambda: datetime.now(timezone.utc), ttl_days=7, page_signals=None):
        self.engine, self.http, self.mx_check, self.clock = engine, http, mx_check, clock
        self.ttl = timedelta(days=ttl_days)
        self.page_signals = page_signals
        self.page_drops: Counter = Counter()

    def collect(self, business_id: str, adapters: tuple[SourceAdapter, ...]) -> dict:
        stats = Counter()
        for adapter in adapters:
            try: signals = adapter.fetch()
            except Exception:
                stats[f'source_error:{type(adapter).__name__}'] += 1
                continue
            for signal in signals:
                stats[f'signal:{signal.source}'] += 1
                try:self.add(business_id, signal)
                except (ValueError, LookupError):stats['invalid_signal'] += 1
        return dict(stats)

    def add(self, business_id: str, signal: RawSignal) -> str:
        require_public_http_url(signal.source_url)
        site = require_public_http_url(signal.site_url)
        domain = registrable_domain(urlsplit(site).hostname or '')
        if not domain or is_platform(domain) or (business_id.startswith('client-zero') and domain == 'evorove.com'):
            raise ValueError('platform_or_domain')
        if not signal.quote or signal.quote not in signal.source_text or signal.signal_date.tzinfo is None:
            raise ValueError('ungrounded_signal')
        now = self.clock()
        with Session(self.engine) as db, db.begin():
            company = db.get(SourceCompany, (business_id, domain))
            if company and company.suppressed:
                return 'suppressed'
            if company is None:
                company = SourceCompany(business_id=business_id,domain=domain,name=signal.company_name[:255],site_url=site)
                db.add(company)
            existing = db.get(SourceSignal, (business_id, signal.source_url))
            if existing is None:
                db.add(SourceSignal(business_id=business_id,source_url=signal.source_url,domain=domain,source=signal.source,
                    signal_type=signal.signal_type,signal_date=signal.signal_date,quote=signal.quote,source_text=signal.source_text,
                    fetched_at=now,context_only=signal.context_only,is_advertisement=signal.is_advertisement,
                    source_id=signal.source_id or signal.source))
            fresh = company.expires_at is not None and _utc(company.expires_at) > now
            if not fresh:
                # Claim the site before any HTTP request, so concurrent runs reuse it.
                company.collected_at, company.expires_at = now, now + self.ttl
        if fresh:
            return 'cached'
        self._collect_site(business_id, domain, site, signal.person_name)
        return 'collected'

    def _collect_site(self, business_id: str, domain: str, site: str, signal_person: str) -> None:
        parsed = urlsplit(site)
        root = f'{parsed.scheme}://{parsed.netloc}/'
        now = self.clock()
        pages = []
        dated = []
        leader_pages = []
        offer = ""
        seen_urls = set()
        raw, home_page, block = self._one_page(root, seen_urls)
        final = getattr(self.http, "final_url", "") or ""
        final_host = urlsplit(final).hostname or "" if final else ""
        if final_host and registrable_domain(final_host) == domain:
            root = f"{urlsplit(final).scheme}://{final_host}/"
        homepage_html = ""
        if block:
            self.page_drops[block] += 1
        elif home_page is not None and home_page.text.strip():
            offer = offer_surface(title=home_page.title, h1=home_page.h1, description=home_page.description, navigation=home_page.navigation)
            homepage_html = raw
            stored = _stored_text(home_page, root)
            pages.append((root, stored))
            dated.append((root, stored, home_page.publication_date or "", home_page.language))
            leader_pages.append((root, home_page.text, home_page.people, home_page.emails))
        if block not in {"blocked_403", "blocked_challenge", "blocked_connection", "blocked_no_text"}:
            for url in company_page_urls(root, homepage_html)[1:]:
                raw_html, page, reason = self._one_page(url, seen_urls)
                if reason in {"blocked_403", "blocked_challenge", "blocked_connection"}:
                    self.page_drops[reason] += 1
                    continue
                if page is None or not page.text.strip():
                    continue
                stored = _stored_text(page, url)
                pages.append((url, stored))
                dated.append((url, stored, page.publication_date or "", page.language))
                leader_pages.append((url, page.text, page.people, page.emails))
        if competitor_offer(offer):
            self.page_drops["competitor"] += 1
            self._mark_competitor(business_id, domain, offer, now)
        else:
            self._apply_page_signal(business_id, domain, dated, now)
        with Session(self.engine) as db, db.begin():
            company = db.get(SourceCompany, (business_id,domain))
            if company is None or company.suppressed:
                return
            # Cache the attempted site visit even if a page failed, preventing retry loops.
            company.collected_at, company.expires_at = now, now + self.ttl
            for url,text in pages:
                db.merge(SourceRawPage(business_id=business_id,domain=domain,url=url,text=text,fetched_at=now,
                    expires_at=now+self.ttl,sha256=content_sha256(text)))
            self._store_person(db, business_id, domain, signal_person, pages, leader_pages)

    def _one_page(self, url: str, seen: set) -> tuple[str, object | None, str]:
        """One attempt. A blocked or empty page is not fetched again."""
        if url in seen:
            return "", None, ""
        seen.add(url)
        try:
            raw = self.http.get(url)
        except Exception as exc:
            return "", None, classify_fetch_error(exc)
        page = extract_html(url, raw)
        if is_challenge_page(page.title, page.text):
            return raw, None, "blocked_challenge"
        if not page.text.strip():
            return raw, page, "blocked_no_text"
        return raw, page, ""

    def _store_person(self, db, business_id: str, domain: str, signal_person: str, pages, leader_pages) -> None:
        decision = heuristic_decision_maker(pages, domain)
        leader = find_named_leader(leader_pages)
        if decision is not None and (not signal_person or decision.name.casefold() == signal_person.casefold()):
            name, role, email, evidence = decision.name, decision.role, decision.email, decision.source_url
        elif leader is not None and (not signal_person or leader[0].casefold() == signal_person.casefold()):
            name, role, evidence = leader
            email = _visible_email(leader_pages, domain, name)
        else:
            return
        key = name.casefold()
        db.merge(SourcePerson(business_id=business_id, domain=domain, name_key=key, name=name, role=role))
        if not email or not _EMAIL.fullmatch(email):
            return
        local, _, host = email.casefold().partition("@")
        if registrable_domain(host) != domain or not self.mx_check(host):
            return
        db.merge(SourceContact(business_id=business_id, domain=domain, email=email.casefold(), name_key=key,
            mx_verified=True, evidence_url=evidence))

    def _apply_page_signal(self, business_id: str, domain: str, dated, now) -> None:
        if self.page_signals is None or not dated:
            return
        found = self.page_signals(dated)
        if not found or found.get("quote") not in (found.get("source_text") or ""):
            return
        published = parse_page_date(found.get("published") or "")
        new_url = found["source_url"]
        with Session(self.engine) as db, db.begin():
            rows = list(db.scalars(select(SourceSignal).where(SourceSignal.business_id==business_id, SourceSignal.domain==domain)))
            target = next((row for row in rows if row.source_id == "searxng_web" or row.signal_type == "company_candidate"), None)
            if target is None:
                return
            # A missing page date stays missing. fetched_at is the observation, not the event.
            fields = dict(signal_type=found["signal_type"], quote=found["quote"],
                source_text=found["source_text"], context_only=published is None, fetched_at=now)
            if published is not None:
                fields["signal_date"] = published
            if target.source_url == new_url:
                for key, value in fields.items():
                    setattr(target, key, value)
                return
            source, source_id, advertisement = target.source, target.source_id or target.source, target.is_advertisement
            db.delete(target)
            db.flush()
            db.add(SourceSignal(business_id=business_id, source_url=new_url, domain=domain, source=source,
                is_advertisement=advertisement, source_id=source_id, **fields))

    def _mark_competitor(self, business_id: str, domain: str, offer: str, now) -> None:
        quote = " ".join((offer or "").split())[:700]
        if not quote:
            return
        with Session(self.engine) as db, db.begin():
            rows = list(db.scalars(select(SourceSignal).where(SourceSignal.business_id==business_id, SourceSignal.domain==domain)))
            target = next((row for row in rows if row.source_id == "searxng_web" or row.signal_type == "company_candidate"), None)
            if target is None:
                return
            target.signal_type = "competitor"
            target.quote = quote
            target.source_text = quote
            target.context_only = True
            target.fetched_at = now

    def suppress_and_erase(self, business_id: str, domain: str) -> None:
        """Erase collected content/PII and retain a domain stop-list tombstone."""
        with Session(self.engine) as db, db.begin():
            company = db.get(SourceCompany,(business_id,domain))
            if company is None:
                company=SourceCompany(business_id=business_id,domain=domain,name='',site_url='',suppressed=True)
                db.add(company)
            else:
                company.name=company.site_url='';company.suppressed=True
                company.collected_at=company.expires_at=None
            for model in (SourceSignal,SourceRawPage,SourceContact,SourcePerson):
                db.execute(delete(model).where(model.business_id==business_id,model.domain==domain))
