from datetime import datetime, timezone, timedelta
from dataclasses import replace
from evorove_lead.warehouse import *
from evorove_lead.reweight_hypotheses import mature_performance, reweight_hypotheses

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)

def cohort(count=30, days=40):
    w = RecordingAnalysisWarehouse()
    w.save_hypothesis(HypothesisRecord(id="h", business_id="a", brief_id="b", audience_segment="SMB", channel="web", query_template="need", intent_trigger="need_statement", status="live", fit_score=0, evidence_score=0, reach_estimate=count, created_at=NOW-timedelta(days=days)))
    for i in range(count):
        w.save_candidate(CandidateRecord(id=str(i), business_id="a", hypothesis_id="h", identity=f"person{i}@example.com", channel="email", reason="need", reason_source="https://example.com", fit=1, evidence=1, addressable=True, decision="cold", decided_at=NOW-timedelta(days=days)))
    return w

def test_pending_does_not_mean_failed_and_duplicate_events_do_not_inflate():
    w = cohort()
    for i in range(4):
        w.save_hypothesis_outcome(HypothesisOutcomeRecord(id=str(i), business_id="a", hypothesis_id="h", case_id="one", outcome="done", recorded_at=NOW))
    p = mature_performance(w, "a", "h", now=NOW)
    assert p.done == 1 and p.pending == 29 and p.resolved == 1
    result = reweight_hypotheses(w, "a")[0]
    assert result.new_budget == result.previous_budget
    assert result.new_status == "live"

def test_immature_and_inconsistent_cohorts_are_not_used():
    w = cohort(count=1, days=1)
    for i in range(2):
        w.save_hypothesis_outcome(HypothesisOutcomeRecord(id=str(i), business_id="a", hypothesis_id="h", case_id=str(i), outcome="done", recorded_at=NOW))
    p = mature_performance(w, "a", "h", now=NOW)
    assert not p.cohort_mature and not p.attribution_complete
