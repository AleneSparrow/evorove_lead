"""Collect public signals and each candidate site once per TTL; no LLM."""
from __future__ import annotations
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from evorove_lead.decision_maker import heuristic_decision_maker
from evorove_lead.page_extract import extract_html, content_sha256
from evorove_lead.platforms import registrable_domain, is_platform
from evorove_lead.presence import require_public_http_url
from evorove_lead.source_adapters import RawSignal, SourceAdapter, PublicSourceHTTP
from evorove_lead.source_core_models import SourceCompany, SourcePerson, SourceContact, SourceRawPage, SourceSignal

_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_GENERIC = {'info','admin','support','hello','contact','sales','office','team','noreply'}

def public_mx_exists(domain: str) -> bool:
    """DNS check during collection; fail closed on timeout/NXDOMAIN/null MX."""
    import dns.resolver
    try:
        answers = dns.resolver.resolve(domain, 'MX', lifetime=3)
        return any(str(answer.exchange).rstrip('.') for answer in answers)
    except Exception:
        return False

def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class SourceCollector:
    def __init__(self, engine, http: PublicSourceHTTP, *, mx_check=public_mx_exists, clock=lambda: datetime.now(timezone.utc), ttl_days=7):
        self.engine, self.http, self.mx_check, self.clock = engine, http, mx_check, clock
        self.ttl = timedelta(days=ttl_days)

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
        urls = [root, *(urljoin(root, slug) for slug in ('about','contact','team'))]
        now = self.clock()
        pages = []
        for url in urls:
            try:
                raw = self.http.get(url)
                page = extract_html(url, raw)
                if page.text.strip(): pages.append((url,page.text))
            except Exception:
                continue
        with Session(self.engine) as db, db.begin():
            company = db.get(SourceCompany, (business_id,domain))
            if company is None or company.suppressed:
                return
            # Cache the attempted site visit even if a page failed, preventing retry loops.
            company.collected_at, company.expires_at = now, now + self.ttl
            for url,text in pages:
                db.merge(SourceRawPage(business_id=business_id,domain=domain,url=url,text=text,fetched_at=now,
                    expires_at=now+self.ttl,sha256=content_sha256(text)))
            decision = heuristic_decision_maker(pages,domain)
            if decision is None or (signal_person and decision.name.casefold() != signal_person.casefold()):
                return
            match = _EMAIL.fullmatch(decision.email)
            if not match or decision.email.split('@')[0].casefold() in _GENERIC:
                return
            if registrable_domain(decision.email.split('@')[1]) != domain or not self.mx_check(decision.email.split('@')[1]):
                return
            key = decision.name.casefold()
            db.merge(SourcePerson(business_id=business_id,domain=domain,name_key=key,name=decision.name,role=decision.role))
            db.merge(SourceContact(business_id=business_id,domain=domain,email=decision.email.casefold(),name_key=key,
                mx_verified=True,evidence_url=decision.source_url))

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
