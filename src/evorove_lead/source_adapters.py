"""Public, keyless source adapters. Collection only; no model or CRM access."""
from __future__ import annotations
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser
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
            kind = {'show':'show_hn_launch','ask':'ask_hn_need','hiring':'hn_hiring'}[feed]
            company = (re.sub(r'^(Show|Ask) HN:\s*', '', title, flags=re.I).split(' – ')[0] if feed == 'show' else (urlsplit(outbound).hostname or '')).strip()[:120]
            result.append(RawSignal(company_name=company, person_name='', site_url=outbound,
                signal_type=kind, signal_date=_date(created), source_url=f'https://news.ycombinator.com/item?id={object_id}',
                quote=(body or title)[:700], source=f'hn_{feed}', source_text=text))
        return tuple(result)

    def fetch(self) -> tuple[RawSignal, ...]:
        if self.feed == 'hiring':
            params = {'tags':'comment','query':'hiring','hitsPerPage':self.max_per_feed}
        else:
            params = {'tags':self.feed+'_hn','hitsPerPage':self.max_per_feed}
        url = 'https://hn.algolia.com/api/v1/search_by_date?' + urlencode(params)
        return self.parse(self.http.get(url), self.feed)
