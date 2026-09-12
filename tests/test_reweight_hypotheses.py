from datetime import datetime, timezone

from evorove_lead.reweight_hypotheses import (
    MIN_SAMPLE_FOR_RATING,
    compute_rates,
    reweight_hypotheses,
)
from evorove_lead.warehouse import (
    CandidateRecord,
    HypothesisOutcomeRecord,
    HypothesisRecord,
    RecordingAnalysisWarehouse,
)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
BIZ = "tenant-a"


def _hypothesis(hyp_id: str, **overrides) -> HypothesisRecord:
    base = dict(
        id=hyp_id,
        business_id=BIZ,
        brief_id="brief-1",
        audience_segment="busy parents",
        channel="web_search",
        query_template="need catering",
        intent_trigger="need_statement",
        status="live",
        fit_score=1.0,
        evidence_score=0.5,
        reach_estimate=5,
        created_at=NOW,
    )
    base.update(overrides)
    return HypothesisRecord(**base)


def _candidate(hyp_id: str, cand_id: str, decision: str) -> CandidateRecord:
    return CandidateRecord(
        id=cand_id,
        business_id=BIZ,
        hypothesis_id=hyp_id,
        identity="jane@example.com",
        channel="email",
        reason="asked publicly",
        reason_source="https://forum.example/1",
        fit=1.0,
        evidence=1.0,
        addressable=True,
        decision=decision,
        decided_at=NOW,
    )


def _outcome(hyp_id: str, case_id: str, outcome: str) -> HypothesisOutcomeRecord:
    return HypothesisOutcomeRecord(
        id=f"out-{case_id}",
        business_id=BIZ,
        hypothesis_id=hyp_id,
        case_id=case_id,
        outcome=outcome,
        recorded_at=NOW,
    )


def test_compute_rates_with_no_data_is_zero_not_a_crash():
    warehouse = RecordingAnalysisWarehouse()
    hypothesis = _hypothesis("hyp-1")

    accept_rate, close_rate, sample_size = compute_rates(warehouse, BIZ, hypothesis)

    assert (accept_rate, close_rate, sample_size) == (0.0, 0.0, 0)


def test_compute_rates_from_candidates_and_outcomes():
    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_candidate(_candidate("hyp-1", "c1", "cold"))
    warehouse.save_candidate(_candidate("hyp-1", "c2", "cold"))
    warehouse.save_candidate(_candidate("hyp-1", "c3", "rejected"))
    warehouse.save_hypothesis_outcome(_outcome("hyp-1", "case-1", "done"))

    accept_rate, close_rate, sample_size = compute_rates(warehouse, BIZ, _hypothesis("hyp-1"))

    assert accept_rate == 2 / 3
    assert close_rate == 1 / 2
    assert sample_size == 2


def test_low_close_rate_pauses_and_zeroes_budget_once_enough_sample():
    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1", query_budget=2))
    for i in range(MIN_SAMPLE_FOR_RATING):
        warehouse.save_candidate(_candidate("hyp-1", f"c{i}", "cold"))
    # No outcomes at all -> close_rate 0.0, well under the pause threshold.

    results = reweight_hypotheses(warehouse, BIZ)

    assert len(results) == 1
    result = results[0]
    assert result.new_status == "paused"
    assert result.new_budget == 0
    saved = warehouse.list_hypotheses(BIZ)[0]
    assert saved.status == "paused"
    assert saved.query_budget == 0


def test_high_close_rate_boosts_budget():
    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1", query_budget=1))
    for i in range(MIN_SAMPLE_FOR_RATING):
        warehouse.save_candidate(_candidate("hyp-1", f"c{i}", "cold"))
        warehouse.save_hypothesis_outcome(_outcome("hyp-1", f"case-{i}", "done"))

    results = reweight_hypotheses(warehouse, BIZ)

    assert results[0].new_status == "live"
    assert results[0].new_budget == 2


def test_budget_never_exceeds_the_cap():
    from evorove_lead.reweight_hypotheses import MAX_QUERY_BUDGET

    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1", query_budget=MAX_QUERY_BUDGET))
    for i in range(MIN_SAMPLE_FOR_RATING):
        warehouse.save_candidate(_candidate("hyp-1", f"c{i}", "cold"))
        warehouse.save_hypothesis_outcome(_outcome("hyp-1", f"case-{i}", "done"))

    results = reweight_hypotheses(warehouse, BIZ)

    assert results[0].new_budget == MAX_QUERY_BUDGET


def test_not_enough_sample_leaves_status_and_budget_untouched():
    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1", query_budget=1))
    warehouse.save_candidate(_candidate("hyp-1", "c0", "cold"))  # below MIN_SAMPLE_FOR_RATING

    results = reweight_hypotheses(warehouse, BIZ)

    assert results[0].new_status == "live"
    assert results[0].new_budget == 1


def test_dead_hypothesis_stays_dead_and_gets_zero_budget():
    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1", status="dead", query_budget=1))

    results = reweight_hypotheses(warehouse, BIZ)

    assert results[0].new_status == "dead"
    assert results[0].new_budget == 0


def test_reweight_feeds_pattern_library_when_archetype_given():
    from evorove_lead.pattern_library import RecordingPatternLibrary

    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1", channel="web_search"))
    for i in range(MIN_SAMPLE_FOR_RATING):
        warehouse.save_candidate(_candidate("hyp-1", f"c{i}", "cold"))
        warehouse.save_hypothesis_outcome(_outcome("hyp-1", f"case-{i}", "done"))
    library = RecordingPatternLibrary()

    reweight_hypotheses(
        warehouse, BIZ, pattern_library=library, business_archetype="catering"
    )

    suggestions = library.suggest_patterns("catering")
    assert len(suggestions) == 1
    assert suggestions[0].channel_family == "web_search"
    assert suggestions[0].query_pattern == "need_statement"
    assert suggestions[0].observed_close_rate_band == "high"


def test_reweight_does_not_feed_pattern_library_below_sample_threshold():
    from evorove_lead.pattern_library import RecordingPatternLibrary

    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1"))
    warehouse.save_candidate(_candidate("hyp-1", "c0", "cold"))
    library = RecordingPatternLibrary()

    reweight_hypotheses(
        warehouse, BIZ, pattern_library=library, business_archetype="catering"
    )

    assert library.suggest_patterns("catering") == ()


def test_reweight_without_archetype_never_touches_pattern_library():
    from evorove_lead.pattern_library import RecordingPatternLibrary

    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-1"))
    for i in range(MIN_SAMPLE_FOR_RATING):
        warehouse.save_candidate(_candidate("hyp-1", f"c{i}", "cold"))
    library = RecordingPatternLibrary()

    reweight_hypotheses(warehouse, BIZ, pattern_library=library)

    assert library.observations == {}


def test_reweight_is_tenant_scoped():
    warehouse = RecordingAnalysisWarehouse()
    warehouse.save_hypothesis(_hypothesis("hyp-a", business_id="tenant-a"))
    warehouse.save_hypothesis(_hypothesis("hyp-b", business_id="tenant-b"))

    results = reweight_hypotheses(warehouse, "tenant-a")

    assert [r.hypothesis_id for r in results] == ["hyp-a"]
