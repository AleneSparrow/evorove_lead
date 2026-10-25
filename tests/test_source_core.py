"""Offline fixtures and deterministic rules for the separate source core."""
from __future__ import annotations
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from evorove_lead.sqlalchemy_models import Base
from evorove_lead.source_core_models import SourceCompany, SourceContact, SourceRawPage, SourceSignal
from evorove_lead.source_adapters import HNAlgoliaAdapter, PublicSourceHTTP, RawSignal
from evorove_lead.source_collection import SourceCollector
from evorove_lead.source_analysis import SourceAnalyzer
from evorove_lead.llm_usage import CompletionReply

FIX = Path(__file__).parent / 'fixtures/source_core'
NOW = datetime(2026,10,25,12,tzinfo=timezone.utc)

def test_saved_hn_samples_have_dated_grounded_quotes():
    show = HNAlgoliaAdapter.parse((FIX/'hn_show.json').read_text(), 'show')
    ask = HNAlgoliaAdapter.parse((FIX/'hn_ask.json').read_text(), 'ask')
    hiring = HNAlgoliaAdapter.parse((FIX/'hn_hiring.json').read_text(), 'hiring')
    assert len(show)==2 and all(s.signal_date.tzinfo and s.quote in s.source_text and s.source_id=='hn_show' for s in show)
    assert all(s.signal_type=='ask_hn_need' and s.quote in s.source_text for s in ask)
    # The saved sample is a search for the word "hiring", not the monthly thread, so it yields nothing.
    assert hiring == ()
    assert HNAlgoliaAdapter.parse('{"hits": [{"title":"Ask HN: no company link","created_at":"2026-10-25T00:00:00Z","objectID":"1"}]}','ask')==()
    comment = {'hits':[{'objectID':'44','created_at':'2026-10-25T00:00:00Z',
        'story_title':'Ask HN: Who is hiring? (October 2026)',
        'comment_text':'We are hiring a first SDR at https://example.com/jobs', 'author':'handle'}]}
    parsed = HNAlgoliaAdapter.parse(json.dumps(comment),'hiring')
    assert len(parsed)==1 and parsed[0].company_name=='example.com'
    assert parsed[0].person_name=='' and parsed[0].quote in parsed[0].source_text and parsed[0].source_id=='hn_hiring'
    github = {'hits':[{'objectID':'45','created_at':'2026-10-25T00:00:00Z','story_title':'Ask HN: Who is hiring? (October 2026)',
        'comment_text':'Library at https://github.com/acme/app and https://huggingface.co/acme/model','author':'handle'}]}
    assert HNAlgoliaAdapter.parse(json.dumps(github),'hiring')==()


def test_who_is_hiring_fetches_the_monthly_thread():
    class HTTP:
        def __init__(self):
            self.urls=[]
        def get(self, url, **kwargs):
            self.urls.append(url)
            if 'ask_hn' in url:
                return json.dumps({'hits':[{'objectID':'999','title':'Ask HN: Who is hiring? (October 2026)','created_at':'2026-10-01T00:00:00Z'}]})
            return json.dumps({'hits':[{'objectID':'1','created_at':'2026-10-20T00:00:00Z',
                'story_title':'Ask HN: Who is hiring? (October 2026)','comment_text':'Acme is hiring at https://example.com/jobs','author':'handle'}]})
    http = HTTP()
    signals = HNAlgoliaAdapter(http, feed='hiring').fetch()
    assert len(http.urls)==2 and 'story_999' in http.urls[1] and 'query=hiring' not in http.urls[1]
    assert len(signals)==1 and signals[0].source_id=='hn_hiring' and signals[0].person_name==''
    empty = HTTP()
    empty.get = lambda url, **kwargs: (empty.urls.append(url) or '{"hits":[{"objectID":"1","title":"Ask HN: something else","created_at":"2026-10-01T00:00:00Z"}]}')
    assert HNAlgoliaAdapter(empty, feed='hiring').fetch()==()
    assert len(empty.urls)==1


class FakeResponse:
    def __init__(self,url,body):self.url=url;self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def geturl(self):return self.url
    def read(self,n):return self.body.encode()[:n]

class FakeHTTP:
    def __init__(self,robots='User-agent: *\nAllow: /'):
        self.urls=[];self.robots=robots
    def __call__(self,request,timeout):
        url=request.full_url;self.urls.append(url)
        if url.endswith('/robots.txt'):return FakeResponse(url,self.robots)
        return FakeResponse(url,'<html><body><p>Jane Doe is founder of Example SaaS. Example SaaS is based in the United States. jane@example.com</p></body></html>')

def test_robots_block_and_one_second_site_spacing():
    fake=FakeHTTP('User-agent: *\nDisallow: /contact')
    tick=[0.0]
    def sleep(n):tick[0]+=n
    http=PublicSourceHTTP(opener=fake,clock=lambda:tick[0],sleep=sleep,host_ok=lambda _:True)
    http.get('https://example.com/')
    http.get('https://example.com/about')
    assert tick[0]>=1.0
    try:http.get('https://example.com/contact')
    except ValueError as exc:assert str(exc)=='robots_disallow'
    else:assert False
    assert 'https://example.com/contact' not in fake.urls

def make_signal(days=1):
    quote='We need customers for our SaaS in the United States.'
    return RawSignal('Example','', 'https://example.com','ask_hn_need',NOW-timedelta(days=days),
        'https://news.ycombinator.com/item?id=123',quote,'hn_ask',quote,source_id='hn_ask')

def setup(monkeypatch):
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    fake=FakeHTTP()
    tick=[0.0]
    http=PublicSourceHTTP(opener=fake,clock=lambda:tick[0],sleep=lambda n:tick.__setitem__(0,tick[0]+n),host_ok=lambda _:True)
    # The collector checks the URL is public. These tests use example hosts and do not resolve DNS.
    monkeypatch.setattr('evorove_lead.source_collection.require_public_http_url', lambda url, host_ok=None: url)
    collector=SourceCollector(engine,http,mx_check=lambda _:True,clock=lambda:NOW)
    return engine,collector,fake

def test_collect_once_contact_mx_and_offline_analysis(monkeypatch):
    engine,collector,fake=setup(monkeypatch)
    assert collector.add('client-zero',make_signal())=='collected'
    site_requests=len(fake.urls)
    assert site_requests<=5  # robots plus at most four site pages
    assert collector.add('client-zero',make_signal())=='cached'
    assert len(fake.urls)==site_requests
    with Session(engine) as db:
        pages=db.scalars(select(SourceRawPage)).all()
        assert len(pages)<=4 and all(len(p.sha256)==64 and p.expires_at.date()>=NOW.date() for p in pages)
        assert db.scalar(select(SourceContact)).mx_verified
        assert db.scalar(select(SourceSignal)).source_id=='hn_ask'
    class Classifier:
        calls=0
        def complete_bounded(self,*,system,user,max_tokens,effort):
            self.calls+=1
            assert effort=='medium' and max_tokens==400
            return CompletionReply(json.dumps({'fit':True,'reason':make_signal().quote}),500,35)
    model=Classifier()
    def network_forbidden(*args,**kwargs):raise AssertionError('analysis tried network')
    monkeypatch.setattr(socket.socket,'connect',network_forbidden)
    report,touches=SourceAnalyzer(engine,model,clock=lambda:NOW).run('client-zero')
    assert report['cold_ready']==1 and report['model_analyzed']==1 and model.calls==1
    assert report['cost_usd']==0.002025
    assert touches[0]['payload']['quote']==make_signal().quote
    assert touches[0]['payload']['signal_type']=='ask_hn_need'
    assert touches[0]['payload']['source_url']==make_signal().source_url
    assert touches[0]['payload']['signal_date'] and touches[0]['payload']['evidence_fetched_at']
    assert touches[0]['cycle']==1 and touches[0]['kind']=='assembled'


def test_fail_closed_no_mx_stale_signal_suppression_and_bad_model_reason(monkeypatch):
    engine,collector,fake=setup(monkeypatch)
    collector.mx_check=lambda _:False
    collector.add('client-zero',make_signal())
    class Model:
        calls=0
        def complete_bounded(self,**kwargs):
            self.calls+=1
            return CompletionReply('{"fit":true,"reason":"Invented revenue growth."}',100,20)
    model=Model()
    report,_=SourceAnalyzer(engine,model,clock=lambda:NOW).run('client-zero')
    assert report['rejections']=={'no_verified_email':1} and model.calls==0
    collector.mx_check=lambda _:True
    with Session(engine) as db, db.begin():
        company=db.get(SourceCompany,('client-zero','example.com'))
        company.expires_at=NOW-timedelta(seconds=1)
    collector.add('client-zero',make_signal())
    report,_=SourceAnalyzer(engine,model,clock=lambda:NOW).run('client-zero')
    assert report['cold_ready']==0 and report['rejections']['ungrounded_reason']==1
    with Session(engine) as db,db.begin():
        signal=db.get(SourceSignal,('client-zero',make_signal().source_url))
        signal.signal_date=NOW-timedelta(days=31)
    report,_=SourceAnalyzer(engine,model,clock=lambda:NOW).run('client-zero')
    assert report['rejections']=={'stale_signal':1}
    collector.suppress_and_erase('client-zero','example.com')
    assert collector.add('client-zero',make_signal())=='suppressed'
    with Session(engine) as db:
        assert db.get(SourceCompany,('client-zero','example.com')).suppressed
        assert not db.scalars(select(SourceRawPage)).all()
        assert not db.scalars(select(SourceContact)).all()
        assert not db.scalars(select(SourceSignal)).all()

def test_same_email_across_companies_is_one_person(monkeypatch):
    from evorove_lead.source_core_models import SourcePerson
    engine,collector,_=setup(monkeypatch)
    collector.add('client-zero',make_signal())
    with Session(engine) as db,db.begin():
        db.add(SourceCompany(business_id='client-zero',domain='example.org',name='Second',site_url='https://example.org',collected_at=NOW,expires_at=NOW+timedelta(days=7)))
        db.add(SourcePerson(business_id='client-zero',domain='example.org',name_key='jane doe',name='Jane Doe',role='founder'))
        db.add(SourceContact(business_id='client-zero',domain='example.org',email='jane@example.com',name_key='jane doe',mx_verified=True,evidence_url='https://example.org/'))
        db.add(SourceRawPage(business_id='client-zero',domain='example.org',url='https://example.org/',text='Second SaaS is based in the United States.',fetched_at=NOW,expires_at=NOW+timedelta(days=7),sha256='a'*64))
        db.add(SourceSignal(business_id='client-zero',source_url='https://news.ycombinator.com/item?id=456',domain='example.org',source='hn_ask',signal_type='ask_hn_need',signal_date=NOW-timedelta(days=1),quote=make_signal().quote,source_text=make_signal().quote,fetched_at=NOW,context_only=False,is_advertisement=False))
    class Model:
        calls=0
        def complete_bounded(self,**kwargs):
            self.calls+=1
            return CompletionReply(json.dumps({'fit':True,'reason':make_signal().quote}),100,20)
    model=Model()
    report,touches=SourceAnalyzer(engine,model,clock=lambda:NOW).run('client-zero')
    assert model.calls==1 and len(touches)==1
    assert report['rejections']['duplicate_person']==1
