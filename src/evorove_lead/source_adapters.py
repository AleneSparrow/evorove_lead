"""Public, keyless source adapters. Collection only; no model or CRM access."""
from __future__ import annotations
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser
from evorove_lead.platforms import is_platform
from evorove_lead.presence import public_urlopen, require_public_http_url

USER_AGENT = 'EvoroveLead/0.1 (+https://evorove.com; public cycle-1 research)'

@dataclass(frozen=True)
class RawSignal:
    company_name: str
    person_name: str
    site_url: str
    signal_type: str
    signal_date: datetime
    source_url: str
    quote: str
    source: str = ''
    source_text: str = ''
    context_only: bool = False
    is_advertisement: bool = False
    source_id: str = ''

class SourceAdapter(Protocol):
    def fetch(self) -> tuple[RawSignal, ...]: ...

class PublicSourceHTTP:
    def __init__(self, *, opener=None, clock=time.monotonic, sleep=time.sleep, max_requests=20, host_ok=None):
        self.opener = opener or public_urlopen(host_ok)
        self.clock, self.sleep = clock, sleep
        self.host_ok = host_ok
        self.max_requests = max_requests
        self.count = 0
        self.last = {}
        self.robots = {}

    def get(self, url: str, *, check_robots=True) -> str:
        url = require_public_http_url(url, host_ok=self.host_ok)
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if check_robots and parsed.path != '/robots.txt':
            parser = self.robots.get(host)
            if parser is None:
                try:
                    body = self.get(f'{parsed.scheme}://{host}/robots.txt', check_robots=False)
                except HTTPError as exc:
                    if exc.code != 404:
                        raise
                    body = ''  # A missing robots.txt permits access; other failures do not.
                parser = RobotFileParser()
                parser.parse(body.splitlines())
                self.robots[host] = parser
            if not parser.can_fetch(USER_AGENT, url):
                raise ValueError('robots_disallow')
        if self.count >= self.max_requests:
            raise RuntimeError('source_request_limit')
        wait = 1.0 - (self.clock() - self.last.get(host, -10.0))
        if wait > 0:
            self.sleep(wait)
        self.count += 1
        self.last[host] = self.clock()
        request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json,text/html;q=0.9,text/plain;q=0.8'})
        with self.opener(request, timeout=12) as response:
            final = require_public_http_url(response.geturl(), host_ok=self.host_ok)
            if check_robots and final != url and (urlsplit(final).hostname != host or not self.robots[host].can_fetch(USER_AGENT, final)):
                raise ValueError('robots_redirect_disallow')
            data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError('source_too_large')
        return data.decode('utf-8', 'replace')

def _date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)

class HNAlgoliaAdapter:
    """Show/Ask/Who-is-hiring posts; author handles are not verified names."""
    def __init__(self, http: PublicSourceHTTP, *, feed="show", max_per_feed=10):
        if feed not in {"show", "ask", "hiring"}: raise ValueError("unknown HN feed")
        self.http, self.feed, self.max_per_feed = http, feed, max_per_feed

    @staticmethod
    def parse(payload: str, feed="show") -> tuple[RawSignal, ...]:
        result = []
        for hit in json.loads(payload).get('hits', []):
            title = (hit.get('title') or hit.get('story_title') or '').strip()
            body = ' '.join(re.sub(r'<[^>]+>', ' ', html.unescape(hit.get('story_text') or hit.get('comment_text') or '')).split())
            text = ' '.join((title + ' ' + body).split())
            created = hit.get('created_at')
            object_id = str(hit.get('objectID') or '')
            if not text or not created or not object_id:
                continue
            if feed == 'show' and not title.lower().startswith('show hn:'): continue
            if feed == 'ask' and not title.lower().startswith('ask hn:'): continue
            if feed == 'hiring' and 'who is hiring' not in (hit.get('story_title') or title).lower(): continue
            outbound = hit.get('url') or hit.get('story_url') or ''
            if not outbound.startswith(('http://', 'https://')):
                match = re.search(r'https?://[^\s<>\"]+', body)
                outbound = match.group(0).rstrip('.,)') if match else ''
            if not outbound.startswith(('http://', 'https://')): continue
            if is_platform(urlsplit(outbound).hostname or ''): continue
            kind = {'show':'show_hn_launch','ask':'ask_hn_need','hiring':'hn_hiring'}[feed]
            company = (re.sub(r'^(Show|Ask) HN:\s*', '', title, flags=re.I).split(' – ')[0] if feed == 'show' else (urlsplit(outbound).hostname or '')).strip()[:120]
            result.append(RawSignal(company_name=company, person_name='', site_url=outbound,
                signal_type=kind, signal_date=_date(created), source_url=f'https://news.ycombinator.com/item?id={object_id}',
                quote=(body or title)[:700], source=f'hn_{feed}', source_text=text, source_id=f'hn_{feed}'))
        return tuple(result)

    @staticmethod
    def latest_hiring_story_id(payload: str) -> str:
        """The current Ask HN: Who is hiring? story, not a recent comment that says hiring."""
        for hit in json.loads(payload).get('hits') or []:
            title = (hit.get('title') or '').strip()
            if re.match(r'^ask hn:\s+who is hiring\?', title, re.I) and hit.get('objectID'):
                return str(hit['objectID'])
        return ''

    def fetch(self) -> tuple[RawSignal, ...]:
        if self.feed == 'hiring':
            story_url = 'https://hn.algolia.com/api/v1/search_by_date?' + urlencode(
                {'tags': 'story,ask_hn', 'query': '"Who is hiring"', 'hitsPerPage': 5})
            story_id = self.latest_hiring_story_id(self.http.get(story_url))
            if not story_id:
                return ()
            # Comments on that story only. A date-sorted search for the word "hiring"
            # returns other threads, and the title filter then keeps none of them.
            params = {'tags': f'comment,story_{story_id}', 'hitsPerPage': self.max_per_feed}
        else:
            params = {'tags': self.feed + '_hn', 'hitsPerPage': self.max_per_feed}
        url = 'https://hn.algolia.com/api/v1/search_by_date?' + urlencode(params)
        return self.parse(self.http.get(url), self.feed)


_FORM_D_ROW = re.compile(r'^(D(?:/A)?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(\S+)')
_HTTP_URL = re.compile(r'https?://[^\s<>"\']+')


def previous_business_day(day):
    """Last weekday before `day`. EDGAR does not publish a daily index on Saturday or Sunday."""
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def form_d_index_url(day) -> str:
    quarter = (day.month - 1) // 3 + 1
    return (f'https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{quarter}/'
            f'form.{day.strftime("%Y%m%d")}.idx')


def parse_form_d_index(payload: str) -> tuple[dict, ...]:
    """Form D rows from an EDGAR daily index. Other form types are ignored."""
    rows = []
    for line in payload.splitlines():
        match = _FORM_D_ROW.match(line.rstrip())
        if match is None:
            continue
        path = match.group(5)
        if path.startswith('/'):
            path = path[1:]
        rows.append({'form': match.group(1), 'company': ' '.join(match.group(2).split()),
            'cik': match.group(3), 'filed': match.group(4), 'path': path})
    return tuple(rows)


def parse_form_d_filing(body: str, *, company: str, filed: str, source_url: str) -> RawSignal | None:
    """A signal only when the filing text itself contains a company website.

    The stored quote is the single line that contains that website. Related-person
    street addresses on other lines are not copied into the warehouse.
    """
    site = ''
    quote_line = ''
    for raw_line in body.splitlines():
        line = ' '.join(re.sub(r'<[^>]+>', ' ', html.unescape(raw_line)).split())
        for found in _HTTP_URL.findall(line):
            url = found.rstrip('.,);]')
            host = urlsplit(url).hostname or ''
            if host.endswith('sec.gov') or is_platform(host):
                continue
            if not url.startswith(('http://', 'https://')):
                continue
            site, quote_line = url, line
            break
        if site:
            break
    if not site or not quote_line or site not in quote_line:
        return None
    try:
        filed_on = datetime.strptime(filed, '%Y%m%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    quote = quote_line[:700]
    return RawSignal(company_name=(company or urlsplit(site).hostname or '')[:120], person_name='',
        site_url=site, signal_type='sec_form_d', signal_date=filed_on, source_url=source_url,
        quote=quote, source='sec_form_d', source_text=quote, source_id='sec_form_d')


class SecFormDAdapter:
    """Recent Form D filings from the EDGAR daily index. No efts host: its robots.txt returned 403."""

    def __init__(self, http: PublicSourceHTTP, *, max_filings=3, clock=None):
        if not 1 <= max_filings <= 5:
            raise ValueError('invalid Form D filing cap')
        self.http, self.max_filings = http, max_filings
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self) -> tuple[RawSignal, ...]:
        day = previous_business_day(self.clock().date())
        rows = parse_form_d_index(self.http.get(form_d_index_url(day)))[:self.max_filings]
        signals = []
        for row in rows:
            url = 'https://www.sec.gov/Archives/' + row['path']
            try:
                body = self.http.get(url)
            except Exception:
                continue
            signal = parse_form_d_filing(body, company=row['company'], filed=row['filed'], source_url=url)
            if signal is not None:
                signals.append(signal)
        return tuple(signals)
