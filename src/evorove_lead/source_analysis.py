"""Offline source-warehouse re-analysis. Only the injected model port may leave process."""
from __future__ import annotations
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from math import ceil
from sqlalchemy import select
from sqlalchemy.orm import Session
from evorove_lead.candidate import accept_candidate
from evorove_lead.crm_touch import assembled_touch, stable_person_id
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.decision_maker import find_named_leader
from evorove_lead.enrichment_queue import enrich_company
from evorove_lead.lead_playbooks import screen_lead
from evorove_lead.source_core_models import SourceCompany, SourcePerson, SourceContact, SourceRawPage, SourceSignal

_US = re.compile(r'\b(?:based|headquartered|located|office|address)\b.{0,65}\b(?:US|USA|United States|U\.S\.|San Francisco|New York|California)\b', re.I)
_FORM = re.compile(r"contact_form (https?://\S+) fields ([A-Za-z0-9_|. -]+)")
_SOFTWARE = re.compile(r'\b(?:SaaS|software|online business|digital product|web app|platform)\b', re.I)
_WAREHOUSE_TEMPERATURE = {
    'sales_hiring': ('hot', 'warm'),
    'ask_hn_need': ('warm', 'cold'),
    'hn_hiring': ('warm', 'cold'),
    'show_hn_launch': ('cold', 'cold'),
    'web_mention': ('cold', 'cold'),
}

def warehouse_temperature(signal_type: str, *, age_days: int, context_only: bool) -> str:
    """Temperature for a stored signal when no playbook is attached. No model."""
    event, context = _WAREHOUSE_TEMPERATURE.get(signal_type, ('cold', 'cold'))
    limit = 90 if context_only else 30
    if age_days < 0 or age_days > limit:
        return 'cold'
    return context if context_only else event

def _contact_form(pages) -> str:
    for page in pages:
        match = _FORM.search(page.text or "")
        if match:
            return match.group(1)
    return ""


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class SourceAnalyzer:
    def __init__(self, engine, classifier=None, *, clock=lambda: datetime.now(timezone.utc), max_candidates=None,
                 max_cost_microusd=None, input_rate=3, output_rate=15, playbook=None):
        self.engine, self.classifier, self.clock, self.playbook = engine, classifier, clock, playbook
        self.max_candidates = int(max_candidates if max_candidates is not None else os.getenv('SOURCE_ANALYZE_TOP_N','5'))
        self.max_cost = int(max_cost_microusd if max_cost_microusd is not None else os.getenv('SOURCE_MAX_COST_MICROUSD','300000'))
        self.input_rate, self.output_rate = input_rate, output_rate
        if not 0 <= self.max_candidates <= 100 or self.max_cost < 0:raise ValueError('invalid analysis limits')

    def run(self, business_id: str) -> tuple[dict, tuple[dict, ...]]:
        """DB is the only evidence input. Produces Cold-ready payloads, never delivers."""
        now = self.clock()
        playbook = self.playbook
        counts = Counter();ranked=[];seen=set();seen_emails=set();cost=0;costs=[];cold=[];scored=[];enrichment=[]
        with Session(self.engine) as db:
            signals = db.scalars(select(SourceSignal).where(SourceSignal.business_id==business_id).order_by(SourceSignal.signal_date.desc())).all()
            for signal in signals:
                company=db.get(SourceCompany,(business_id,signal.domain))
                if company is None or company.suppressed:
                    counts['suppressed']+=1;continue
                if signal.domain in seen:
                    counts['duplicate_company']+=1;continue
                seen.add(signal.domain)
                if signal.is_advertisement:
                    counts['advertisement']+=1;continue
                age=now-_utc(signal.fetched_at if signal.context_only else signal.signal_date)
                if playbook is None:
                    window = 90 if signal.context_only else 30
                else:
                    window = playbook.context_days
                if age<timedelta(0) or age>timedelta(days=window):
                    counts['stale_signal']+=1;continue
                pages=db.scalars(select(SourceRawPage).where(SourceRawPage.business_id==business_id,SourceRawPage.domain==signal.domain)).all()
                fresh=[p for p in pages if _utc(p.expires_at)>now and p.sha256 and p.text]
                if not fresh:
                    counts['no_fresh_page']+=1;continue
                people=db.scalars(select(SourcePerson).where(SourcePerson.business_id==business_id,SourcePerson.domain==signal.domain,SourcePerson.suppressed.is_(False))).all()
                people_by_key={p.name_key:p for p in people}
                contacts=db.scalars(select(SourceContact).where(SourceContact.business_id==business_id,SourceContact.domain==signal.domain,SourceContact.suppressed.is_(False),SourceContact.mx_verified.is_(True))).all()
                page_urls={p.url for p in fresh}
                if playbook is None:
                    pair=next(((people_by_key[c.name_key],c) for c in contacts if c.name_key in people_by_key and c.email and c.evidence_url in page_urls),None)
                    if pair is None:
                        counts['no_verified_email']+=1;continue
                    person,contact=pair
                    contact_channel, contact_value = "email", contact.email
                else:
                    person = next(iter(people), None)
                    contact = next((c for c in contacts if c.email and c.evidence_url in page_urls), None)
                    form = _contact_form(fresh)
                    if contact is not None:
                        contact_channel, contact_value = "email", contact.email
                    elif form:
                        contact_channel, contact_value = "form", form
                    else:
                        contact_channel, contact_value = "", ""
                    if person is None:
                        found = find_named_leader(tuple((p.url, p.text, ()) for p in fresh))
                        person_name, person_role = (found[0], found[1]) if found else ("", "")
                    else:
                        person_name, person_role = person.name, person.role
                evidence=' '.join([signal.source_text,*[p.text for p in fresh]])
                if playbook is not None and signal.signal_type == "competitor":
                    counts['competitor']+=1;continue
                if playbook is None:
                    us_ok = any(_US.search(p.text) for p in fresh)
                    if not us_ok:
                        counts['country_not_confirmed']+=1;continue
                if signal.quote not in signal.source_text:
                    counts['ungrounded_quote']+=1;continue
                if playbook is None:
                    if not _SOFTWARE.search(evidence):
                        counts['not_saas_or_online']+=1;continue
                    temperature = warehouse_temperature(signal.signal_type, age_days=age.days, context_only=bool(signal.context_only))
                    segment, hypothesis, reason = 'small US SaaS or online business', 'source-core', ''
                    kind = signal.signal_type
                else:
                    decision = screen_lead(playbook, evidence=evidence, domain=signal.domain, quote=signal.quote,
                        age_days=age.days, context_only=bool(signal.context_only), person_name=person_name,
                        person_role=person_role, email_mx=contact is not None and bool(contact.email),
                        contact_form=form)
                    if decision.rejection:
                        counts[decision.rejection]+=1;continue
                    if decision.level == "B":
                        enriched = enrich_company(domain=signal.domain, pages=tuple((p.url, p.text) for p in fresh),
                            gaps=decision.gaps)
                        enrichment.append({"domain": signal.domain, **enriched.as_dict()})
                        if enriched.person_name or enriched.contact_form or enriched.quote:
                            decision = screen_lead(playbook, evidence=evidence, domain=signal.domain, quote=signal.quote or enriched.quote,
                                age_days=age.days, context_only=bool(signal.context_only),
                                person_name=enriched.person_name or person_name,
                                person_role=enriched.person_role or person_role,
                                email_mx=contact is not None and bool(getattr(contact, "email", "")),
                                contact_form=enriched.contact_form or form)
                            if enriched.contact_form and not contact_value:
                                contact_channel, contact_value = "form", enriched.contact_form
                            if enriched.person_name:
                                person_name, person_role = enriched.person_name, enriched.person_role or person_role
                    counts[f"level_{decision.level}"] += 1
                    scored.append({"domain": signal.domain, "fit": decision.fit, "intent": decision.intent,
                        "reach": decision.reach, "total": decision.total, "level": decision.level,
                        "temperature": decision.temperature, "gaps": list(decision.gaps),
                        "contact_channel": contact_channel, "contact_value": contact_value,
                        "person": person_name, "role": person_role})
                    if decision.level != "A" or not decision.reason:
                        counts['rules_passed']+=1
                        continue
                    temperature, segment, hypothesis, reason = decision.temperature, decision.segment, playbook.id, decision.reason
                    kind = decision.kind
                    if person is None:
                        person = type("Named", (), {"name": person_name or company.name})()
                if contact_channel == "email" and contact_value.casefold() in seen_emails:
                    counts['duplicate_person']+=1;continue
                if contact_channel == "email":
                    seen_emails.add(contact_value.casefold())
                if playbook is None:
                    score={'ask_hn_need':90,'hn_hiring':70,'sales_hiring':70,'show_hn_launch':40}.get(signal.signal_type,30)
                else:
                    score={'sales_hire':90,'open_sales_role':88,'demand_complaint':84,'new_offer':60,'team_growth':55,'marketing_change':50}.get(kind,30)
                score+=max(0,30-age.days)
                ranked.append((score,signal,company,person,contact,max(fresh,key=lambda p:p.fetched_at),temperature,segment,hypothesis,reason,kind,contact_channel,contact_value,tuple(getattr(decision,'quotes',()) if playbook is not None else ())))
                counts['rules_passed']+=1
        ranked.sort(key=lambda row:(-row[0],row[1].source_url))
        for score,signal,company,person,contact,page,temperature,segment,hypothesis,reason,kind,contact_channel,contact_value,quotes in ranked[:self.max_candidates]:
            if playbook is not None:
                identity = contact_value if contact_channel == "email" else person.name
                person_key = stable_person_id(business_id, email=contact_value if contact_channel == "email" else None, identity=person.name)
                candidate=accept_candidate(identity=identity,reason=reason,reason_source=signal.source_url)
                handoff=Cycle1Handoff.from_candidate(candidate,contact_channel,person_id=person_key,hypothesis_id=hypothesis)
                touch=assembled_touch(business_id,candidate,handoff,segment=segment)
                shown_date = "" if signal.context_only else _utc(signal.signal_date).isoformat()
                touch['payload'].update(signal_type=signal.signal_type,signal_date=shown_date,
                    source_url=signal.source_url,quote=signal.quote,reason=reason,reason_quotes=list(quotes or (reason,)),
                    evidence_fetched_at=_utc(signal.fetched_at).isoformat(),
                    source_id=signal.source_id or signal.source,signal_temperature=temperature,temperature=temperature,
                    detected_signal=kind,contact_channel=contact_channel,contact_value=contact_value,
                    fit=decision_score(scored, signal.domain, "fit"),
                    intent=decision_score(scored, signal.domain, "intent"),
                    reach=decision_score(scored, signal.domain, "reach"),
                    total=decision_score(scored, signal.domain, "total"),
                    level=decision_score(scored, signal.domain, "level"),
                    gaps=decision_score(scored, signal.domain, "gaps"))
                cold.append(touch);counts['cold_ready']+=1
                continue
            if self.classifier is None:
                counts['model_unavailable']+=1;continue
            context={'company':company.name,'person':person.name,'role':person.role,'signal_type':signal.signal_type,
                'signal_date':_utc(signal.signal_date).date().isoformat(),'source_url':signal.source_url,
                'quote':signal.quote[:700],'site_fact':page.text[:600]}
            user=json.dumps(context,ensure_ascii=False)
            reserved=ceil((len(user.encode())+800)*self.input_rate+400*self.output_rate)
            if cost+reserved>self.max_cost:
                counts['cost_cap']+=1;break
            reply=self.classifier.complete_bounded(system=(
                'Return JSON {"fit": boolean, "reason": string}. Decide if this small US SaaS/online business has a grounded need for Evorove lead generation. '
                'The reason must be exactly one complete sentence copied verbatim from quote. No new facts, prices, promises, or outreach.'),
                user=user,max_tokens=400,effort='medium')
            actual=ceil(reply.input_tokens*self.input_rate+reply.output_tokens*self.output_rate)
            cost+=actual;costs.append(actual/1_000_000);counts['model_analyzed']+=1
            try: verdict=json.loads(reply.text)
            except ValueError:counts['invalid_model_reply']+=1;continue
            reason=verdict.get('reason','') if isinstance(verdict,dict) else ''
            if not isinstance(verdict,dict) or verdict.get('fit') is not True or not isinstance(reason,str):
                counts['model_no_fit']+=1;continue
            reason=reason.strip()
            if not reason or reason not in signal.quote or not reason.endswith(('.','!','?')):
                counts['ungrounded_reason']+=1;continue
            candidate=accept_candidate(identity=contact.email,reason=reason,reason_source=signal.source_url)
            handoff=Cycle1Handoff.from_candidate(candidate,'email',person_id=stable_person_id(business_id,email=contact.email),hypothesis_id='source-core')
            touch=assembled_touch(business_id,candidate,handoff,segment='small US SaaS or online business')
            touch['payload'].update(signal_type=signal.signal_type,signal_date=_utc(signal.signal_date).isoformat(),
                source_url=signal.source_url,quote=signal.quote,reason=reason,evidence_fetched_at=_utc(signal.fetched_at).isoformat(),
                source_id=signal.source_id or signal.source,signal_temperature=temperature,detected_signal=kind)
            cold.append(touch);counts['cold_ready']+=1
        kept = {'rules_passed','model_analyzed','cold_ready'}
        report={'candidates_with_pain':0, 'candidates_without_pain':len(cold), 'pain_ratio':0.0 if cold else None, 'signals':len(signals),'rejections':{k:v for k,v in counts.items() if k not in kept and not str(k).startswith('level_')},
            'rules_passed':counts['rules_passed'],'model_analyzed':counts['model_analyzed'],'cold_ready':counts['cold_ready'],
            'levels':{'A':counts['level_A'],'B':counts['level_B'],'C':counts['level_C']},
            'scored':scored,'enrichment':enrichment,
            'cost_usd':cost/1_000_000,'cost_usd_per_candidate':cost/1_000_000/counts['model_analyzed'] if counts['model_analyzed'] else None,
            'cost_usd_per_lead':cost/1_000_000/len(cold) if cold else None,'candidate_costs_usd':costs}
        return report,tuple(cold)


def decision_score(rows, domain, key):
    for row in rows:
        if row["domain"] == domain:
            return row[key]
    return [] if key == "gaps" else 0
