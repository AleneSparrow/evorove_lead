from evorove_lead.hypothesis import (
    GeoRadius,
    Hypothesis,
    IntentTrigger,
    build_hypotheses,
    prioritize_hypotheses,
    verify_hypothesis,
)
from evorove_lead.offer import GroundedClaim, OfferUnderstanding, accept_offer_understanding
from evorove_lead.materials import DepositedMaterial
from evorove_lead.search import PeopleHit

SITE = DepositedMaterial(
    name="site",
    body="Weekend catering for busy parents. Also for local event planners.",
)


def _offer():
    return accept_offer_understanding(
        materials=(SITE,),
        what_we_sell=(GroundedClaim(text="Weekend catering", source_name="site"),),
        who_may_fit=(
            GroundedClaim(text="busy parents", source_name="site"),
            GroundedClaim(text="local event planners", source_name="site"),
        ),
    )


def test_intent_trigger_rejects_unknown_kind():
    import pytest

    with pytest.raises(ValueError):
        IntentTrigger(kind="made_up", description="anything")


def test_intent_trigger_requires_description():
    import pytest

    with pytest.raises(ValueError):
        IntentTrigger(kind="demographic_fit", description="  ")


def test_build_hypotheses_has_one_demographic_hypothesis_per_audience_claim():
    offer = _offer()

    hypotheses = build_hypotheses(offer, GeoRadius())

    demographic = [h for h in hypotheses if h.intent_trigger.kind == "demographic_fit"]
    assert {h.audience_segment for h in demographic} == {"busy parents", "local event planners"}
    assert all(h.channel == "web_search" for h in demographic)
    assert all("Weekend catering" in h.query_template for h in demographic)


def test_build_hypotheses_has_at_least_two_intent_trigger_hypotheses():
    offer = _offer()

    hypotheses = build_hypotheses(offer, GeoRadius())

    trigger_kinds = {h.intent_trigger.kind for h in hypotheses} - {"demographic_fit"}
    assert trigger_kinds == {"public_ask", "need_statement"}
    for hypothesis in hypotheses:
        if hypothesis.intent_trigger.kind in trigger_kinds:
            assert "Weekend catering" in hypothesis.query_template


def test_build_hypotheses_carries_geo_radius_into_query():
    offer = _offer()

    hypotheses = build_hypotheses(offer, GeoRadius(locality="Austin"))

    assert all("near Austin" in h.query_template for h in hypotheses)
    assert all(h.geo_radius.locality == "Austin" for h in hypotheses)


def test_build_hypotheses_requires_service_and_audience():
    import pytest

    no_service = OfferUnderstanding(
        what_we_sell=(),
        who_may_fit=(GroundedClaim(text="busy parents", source_name="site"),),
        commercial_claims=(),
        must_not_promise=(),
    )
    no_audience = OfferUnderstanding(
        what_we_sell=(GroundedClaim(text="Weekend catering", source_name="site"),),
        who_may_fit=(),
        commercial_claims=(),
        must_not_promise=(),
    )

    with pytest.raises(ValueError):
        build_hypotheses(no_service, GeoRadius())
    with pytest.raises(ValueError):
        build_hypotheses(no_audience, GeoRadius())


class FakeProbe:
    def __init__(self, hits: tuple[PeopleHit, ...]) -> None:
        self.hits = hits
        self.calls = 0

    def probe(self, hypothesis: Hypothesis):
        self.calls += 1
        return self.hits


def _hypothesis(**overrides) -> Hypothesis:
    base = dict(
        audience_segment="busy parents",
        channel="web_search",
        query_template='"need Weekend catering"',
        intent_trigger=IntentTrigger(kind="need_statement", description="asks for it"),
        geo_radius=GeoRadius(),
    )
    base.update(overrides)
    return Hypothesis(**base)


def test_verify_hypothesis_marks_empty_probe_dead():
    hypothesis = _hypothesis()

    result = verify_hypothesis(hypothesis, FakeProbe(()))

    assert result.status == "dead"
    assert result.reach_estimate == 0


def test_verify_hypothesis_marks_garbage_only_probe_dead():
    hypothesis = _hypothesis()
    garbage = (PeopleHit(identity="555-0100", observed_fact="", observed_source="phone dump"),)

    result = verify_hypothesis(hypothesis, FakeProbe(garbage))

    assert result.status == "dead"


def test_verify_hypothesis_marks_real_hit_live_with_measured_scores():
    hypothesis = _hypothesis()
    hits = (
        PeopleHit(
            identity="Jordan Lee, jordan@example.com",
            observed_fact="Publicly asked for weekend catering recommendations.",
            observed_source="https://forum.example/thread/1",
        ),
        PeopleHit(identity="555-0100", observed_fact="", observed_source="phone dump"),
    )

    result = verify_hypothesis(hypothesis, FakeProbe(hits))

    assert result.status == "live"
    assert result.reach_estimate == 1
    assert result.evidence_score == 0.5
    assert result.fit_score == 1.0


def test_build_hypotheses_uses_pattern_library_to_order_by_archetype_history():
    from evorove_lead.pattern_library import RecordingPatternLibrary

    library = RecordingPatternLibrary()
    library.record_observation(
        business_archetype="catering",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.9,
        sample_size=10,
    )
    library.record_observation(
        business_archetype="catering",
        channel_family="web_search",
        query_pattern="demographic_fit",
        close_rate=0.02,
        sample_size=10,
    )
    offer = _offer()

    hypotheses = build_hypotheses(
        offer, GeoRadius(), pattern_library=library, business_archetype="catering"
    )

    kinds_in_order = [h.intent_trigger.kind for h in hypotheses]
    # need_statement has a "high" band pattern -> first. public_ask has no
    # recorded pattern at all -> ranks below even a "low"-band demographic_fit.
    assert kinds_in_order[0] == "need_statement"
    assert kinds_in_order[-1] == "public_ask"
    # Reordering only -- fit_score/evidence_score stay honestly unverified.
    assert all(h.fit_score == 0.0 and h.evidence_score == 0.0 for h in hypotheses)


def test_build_hypotheses_without_archetype_ignores_pattern_library():
    from evorove_lead.pattern_library import RecordingPatternLibrary

    library = RecordingPatternLibrary()
    library.record_observation(
        business_archetype="catering",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.9,
        sample_size=10,
    )
    offer = _offer()

    with_library = build_hypotheses(offer, GeoRadius(), pattern_library=library)
    without_library = build_hypotheses(offer, GeoRadius())

    assert with_library == without_library


def test_prioritize_hypotheses_drops_dead_and_unverified_and_sorts_live():
    dead = _hypothesis(status="dead")
    unverified = _hypothesis(status="live", reach_estimate=0)
    strong = _hypothesis(
        status="live", fit_score=1.0, evidence_score=0.9, reach_estimate=5
    )
    weak = _hypothesis(status="live", fit_score=1.0, evidence_score=0.3, reach_estimate=1)

    ranked = prioritize_hypotheses((dead, unverified, weak, strong))

    assert ranked == (strong, weak)
