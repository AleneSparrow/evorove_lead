from dataclasses import asdict, replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from evorove_lead.candidate import Candidate
from evorove_lead.crm_touch import assembled_touch, RecordingLeadTouchSink
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.observed_pain import attach_observed_pain, validate_pain, pain_counts
from evorove_lead.offer import GroundedClaim, OfferUnderstanding
from evorove_lead.sqlalchemy_warehouse import SqlAlchemyAnalysisWarehouse, create_all
from evorove_lead.warehouse import CandidateRecord, RecordingAnalysisWarehouse

URL = 'https://buyer.example/reviews'
PAIN = 'Customer requests go unanswered until the next day and people book other companies.'
OFFER = OfferUnderstanding((GroundedClaim('Automated replies', 'site'),),
                           (GroundedClaim('businesses', 'site'),), (), ())


def candidate(reason=PAIN):
    return Candidate('owner@buyer.example', reason, URL)


def test_grounded_pain_kept_with_local_verbatim_evidence():
    result = attach_observed_pain(candidate(), OFFER, ((URL, 'Reviews:\n' + PAIN),))
    assert result.observed_pain == result.pain_evidence_excerpt == PAIN
    assert result.pain_evidence_source == URL


@pytest.mark.parametrize('text', ['slow replies', 'needs automation', 'busy owner',
    'All businesses need automation.',
    'If customer requests go unanswered until the next day and people book other companies.',
    'Our customer requests go unanswered until the next day and people book other companies.',
    'Customer requests go unanswered until the next day and people never book other companies.',
    'Customer requests go unanswered until the next day.',
    'Medical requests go unanswered until the next day and people book other companies.'])
def test_labels_hypotheticals_personal_and_missing_cost_rejected(text):
    assert not attach_observed_pain(candidate(text), OFFER, ((URL, text),)).observed_pain


def test_exact_excerpt_guard_rejects_hallucination_and_normalized_quote():
    for source in ('Different page text.', PAIN.replace('go unanswered', 'go  unanswered')):
        assert validate_pain(pain=PAIN, excerpt=PAIN, source_url=URL,
                             source_text=source, offer=OFFER) == ''


def test_offer_fit_uses_only_sold_capabilities_not_audience_or_model_assertion():
    other = replace(OFFER, what_we_sell=(GroundedClaim('AC repair', 'site'),),
                    who_may_fit=(GroundedClaim('Automated replies', 'site'),))
    assert not attach_observed_pain(candidate(), other, ((URL, PAIN),)).observed_pain


def test_length_limit_and_no_invented_paraphrase():
    long = PAIN[:-1] + ' and ' + 'customers ' * 60 + '.'
    for pain, excerpt in ((long, long), ('The business loses customers due to delayed responses.', PAIN)):
        assert not validate_pain(pain=pain, excerpt=excerpt, source_url=URL,
                                 source_text=excerpt, offer=OFFER)


def test_unrelated_sentence_on_candidate_page_not_promoted():
    assert not attach_observed_pain(candidate('The company is hiring.'), OFFER, ((URL, PAIN),)).observed_pain


def test_handoff_and_crm_add_only_pain_no_local_evidence():
    base = candidate()
    enriched = attach_observed_pain(base, OFFER, ((URL, PAIN),))
    baseline = assembled_touch('tenant', base, Cycle1Handoff.from_candidate(base))
    handoff = Cycle1Handoff.from_candidate(enriched)
    touch = assembled_touch('tenant', enriched, handoff)
    assert set(asdict(handoff)) == {'identity', 'reason', 'reason_source', 'channel',
                                   'person_id', 'hypothesis_id', 'observed_pain'}
    expected = {**baseline, 'payload': {**baseline['payload'], 'observed_pain': PAIN}}
    assert touch == expected
    assert 'pain_evidence_excerpt' not in str(touch)
    assert 'pain_evidence_source' not in str(touch)


def test_engine_keeps_cold_without_pain_and_reports_ratio(monkeypatch):
    from evorove_lead.engine import LeadGenerationEngine
    from evorove_lead.business import BusinessSeed
    from evorove_lead.search import PeopleHit, TraceFinding
    from evorove_lead.selection import Selection
    from evorove_lead.materials import DepositedMaterial
    import evorove_lead.engine as module
    # Qualification is outside this test; preserve its result and exercise the real
    # post-qualification path through storage, dedup, handoff and CRM serialization.
    monkeypatch.setattr(module, '_accept_hit', lambda hit, *a, **kw:
                        (Candidate(hit.identity, hit.observed_fact, hit.observed_source),
                         Selection(True, 1.0, 1.0, 'fixture qualification')))
    class Search:
        connected = True
        def find(self, hypothesis):
            return tuple(TraceFinding(URL, text, 'query', 'web_search',
                hit=PeopleHit(email, text, URL, 'email')) for email, text in
                [('a@buyer.example', PAIN), ('b@buyer.example', 'The company has a public business address.')])
    warehouse, sink = RecordingAnalysisWarehouse(), RecordingLeadTouchSink()
    engine = LeadGenerationEngine(presence=None, hypothesis_search=Search(),
                                  warehouse=warehouse, lead_touch_sink=sink)
    result = engine._generate_via_hypotheses(BusinessSeed(site_url='https://seller.example', business_id='a'),
                                           OFFER, (DepositedMaterial('site', 'Automated replies for businesses.'),))
    assert len(result.candidates) == len(result.handoffs) == 2
    assert result.pain_counts == {'candidates_with_pain': 1, 'candidates_without_pain': 1, 'pain_ratio': .5}
    assert len(sink.published) == 2
    assert len([r for r in warehouse.candidates if r.decision == 'cold']) == 2
    row = next(r for r in warehouse.candidates if r.observed_pain)
    assert row.pain_evidence_excerpt in warehouse.traces[0].raw_text
    assert pain_counts(())['pain_ratio'] is None


def test_sqlite_candidate_evidence_round_trip_is_tenant_scoped():
    from test_warehouse import _brief, _hypothesis
    db = create_engine('sqlite://')
    create_all(db)
    warehouse = SqlAlchemyAnalysisWarehouse(db)
    warehouse.save_brief(_brief('a', 'brief'))
    warehouse.save_hypothesis(_hypothesis('a', 'brief', 'hyp'))
    record = CandidateRecord('candidate', 'a', 'hyp', 'owner@buyer.example', 'email', PAIN,
        URL, 1., 1., True, 'cold', datetime.now(timezone.utc), observed_pain=PAIN,
        pain_evidence_excerpt=PAIN, pain_evidence_source=URL)
    warehouse.save_candidate(record)
    found = warehouse.list_candidates('a', 'hyp')[0]
    assert found.observed_pain == found.pain_evidence_excerpt == PAIN
    assert found.pain_evidence_source == URL
    assert warehouse.list_candidates('b', 'hyp') == ()


def test_negated_offer_capability_does_not_authorize_pain():
    offer = replace(OFFER, what_we_sell=(GroundedClaim('No automated replies are provided', 'site'),))
    assert not attach_observed_pain(candidate(), offer, ((URL, PAIN),)).observed_pain


def test_documented_crm_projection_fits_actual_cycle_two_contract():
    """Read schemas only; CRM still needs to implement this projection itself."""
    import ast
    from pathlib import Path
    from typing import Annotated
    from pydantic import BaseModel, ConfigDict, Field, ValidationError
    sibling = Path(__file__).resolve().parents[2] / 'evorove/src/api/schemas.py'
    if not sibling.exists():
        pytest.skip('cycle-2 sibling checkout unavailable')
    tree = ast.parse(sibling.read_text())
    names = {'ApiModel', 'CrmFoundIdentitySchema', 'CrmFoundIngestRequest'}
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names]
    assert len(classes) == 3
    namespace = dict(Annotated=Annotated, BaseModel=BaseModel, ConfigDict=ConfigDict, Field=Field)
    exec(compile(ast.Module(body=classes, type_ignores=[]), 'cycle-two-schema', 'exec'), namespace)
    schema = namespace['CrmFoundIngestRequest']
    enriched = attach_observed_pain(candidate(), OFFER, ((URL, PAIN),))
    touch = assembled_touch('tenant', enriched, Cycle1Handoff.from_candidate(enriched))
    payload = touch['payload']
    # This repo publishes lead-touches, never POST /found. This is the exact
    # projection CRM must provide; forwarding the whole payload would be invalid.
    found = dict(schema_version='1', person_id=touch['person_id'], reason=payload['reason'],
                 source=payload['reason_source'], channel=payload['channel'],
                 identity=touch['identity'], observed_pain=payload['observed_pain'])
    assert schema.model_validate(found).observed_pain == PAIN
    assert set(found) <= {'schema_version', 'person_id', 'reason', 'source', 'preferred_channel',
                          'channel', 'consent_basis', 'identity', 'idempotency_key', 'observed_pain'}
    for extra in ('pain_evidence_excerpt', 'pain_evidence_source', 'hypothesis_id'):
        with pytest.raises(ValidationError):
            schema.model_validate({**found, extra: 'must stay local'})


def test_research_reanalysis_persists_pain_and_run_metric(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_CRM_DELIVERY", "1")
    monkeypatch.setenv("CRM_CYCLE2_ISOLATED", "1")
    import intelligence_helpers as fixtures
    from test_research_pipeline import setup_pipeline
    from evorove_lead.business import BusinessSeed
    monkeypatch.setattr(fixtures, 'SELLER', 'Automated replies for businesses in the US.')
    monkeypatch.setattr(fixtures, 'BUYER',
                        'Jane Owner jane@buyer.example runs a business in the US. ' + PAIN)
    db, pipeline, engine, crm, store = setup_pipeline(tmp_path)
    result = engine.generate(BusinessSeed(site_url='https://seller.example', business_id='a'))
    assert len(result.handoffs) == 1, pipeline.last_run
    assert result.handoffs[0].observed_pain == PAIN
    assert pipeline.last_run['candidates_with_pain'] == 1
    assert pipeline.last_run['candidates_without_pain'] == 0
    rows = engine._warehouse.list_candidates('a', result.handoffs[0].hypothesis_id)
    assert rows[0].observed_pain == rows[0].pain_evidence_excerpt == PAIN
    assert rows[0].pain_evidence_source == 'https://buyer.example/news'
    payload = next(iter(crm.accepted.values()))['payload']
    assert payload['observed_pain'] == PAIN
    assert 'pain_evidence_excerpt' not in payload
