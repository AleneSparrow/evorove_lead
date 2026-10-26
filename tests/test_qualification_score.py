"""Weights, thresholds, and disqualifiers. No model and no network."""
from datetime import datetime, timezone

from evorove_lead.qualification_score import (
    ScoreInput, level_for_total, load_score_model, score_company, strong_non_us,
)

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def _score(evidence, **kwargs):
    return score_company(ScoreInput(evidence=evidence, now=NOW, **kwargs))


def test_every_weight_is_the_published_number_and_axes_do_not_stack():
    model = load_score_model()
    assert [group["points"] if group["id"] != "us" else group["points_by_strength"]
            for group in model["fit"]["groups"]] == [
        18, {"strong": 14, "weak": 6}, 8, 10,
    ]
    assert [item["points"] for item in model["intent"]["items"]] == [8, 6, 5, 4, 4, 3]
    assert [item["points"] for item in model["reach"]["items"]] == [10, 6, 4]
    assert model["fit"]["max"] == 50 and model["intent"]["max"] == 30 and model["reach"]["max"] == 20

    services = _score("Northwind is a marketing agency.")
    software = _score("Northwind sells construction software.")
    both = _score("Northwind is a marketing agency that sells construction software.")
    assert services.fit == software.fit == both.fit == 18
    assert services.rejection == "" and "b2b_services" in services.matched
    assert "b2b_software" in software.matched
    assert "b2b_software" not in both.matched

    strong = _score("Our office is in Ohio.")
    weak = _score("Plans cost $8000 a year.")
    stacked = _score("Our office is in Ohio. Plans cost $8000 a year.")
    assert strong.fit == 14 and strong.rejection == "" and "us_strong" in strong.matched
    assert weak.fit == 6 and weak.rejection == "" and "us_weak" in weak.matched and "us_strong" not in weak.matched
    assert stacked.fit == 14 and "us_weak" not in stacked.matched
    assert _score("Book a call for B2B teams.").rejection == ""
    assert "us_strong" not in _score("Book a call for B2B teams.").matched
    assert "us_weak" not in _score("Book a call for B2B teams.").matched

    assert _score("A team of 40 people works here.").fit == 8
    assert _score("Our offices in three cities.").fit == 8
    assert _score("A team of 500 people works here.").fit == 0
    assert _score("Contact us for pricing.").fit == 10

    assert _score("Book a call.").intent == 8
    assert _score("We are hiring an SDR.").intent == 6
    assert _score("We are growing the team.").intent == 5
    assert _score("We launched a new service.").intent == 4
    published = _score("We published a case study.")
    assert published.intent == 4 and published.fit == 10
    fresh = _score("The note is dated October 1, 2026.")
    assert fresh.intent == 3 and "fresh_dated" in fresh.matched
    assert _score("The note is dated January 1, 2020.").intent == 0

    named = _score("Hello.", person_name="Jane Doe", person_role="Founder")
    mailed = _score("Hello.", email_mx=True)
    form = _score("Hello.", contact_form="https://northwind.example/contact")
    assert named.reach == 10 and mailed.reach == 6 and form.reach == 4
    assert _score("Hello.", person_name="Jane Doe", person_role="intern").reach == 0


def test_thresholds_are_70_and_50():
    assert level_for_total(69) == "B"
    assert level_for_total(70) == "A"
    assert level_for_total(49) == "C"
    assert level_for_total(50) == "B"
    ready = _score(
        "Northwind is a marketing agency. Our office is in Ohio. A team of 40 people. "
        "Engagements from $6,000. Book a call. We are hiring an SDR.",
        email_mx=True,
    )
    assert ready.total == 70 and ready.level == "A" and ready.fit == 50 and ready.intent == 14 and ready.reach == 6
    border = _score("Northwind is a marketing agency. Our office is in Ohio. Book a call.")
    assert border.total == 50 and border.level == "B"
    below = _score("Northwind is a marketing agency. Book a call.")
    assert below.total == 36 and below.level == "C"
    assert below.total < 50 and ready.total >= 70


def test_each_disqualifier_is_binary_and_scores_nothing():
    cases = [
        ("We sell lead generation to other firms.", {}, "competitor"),
        ("Shop now for homeowners.", {}, "b2c"),
        ("Visit our dental practice.", {}, "local_consumer"),
        ("Our law firm takes new matters.", {}, "regulated"),
        ("Our office is in Ohio.", {"schema": "GB"}, "not_us"),
        ("Northwind is a UK B2B marketing agency.", {}, "not_us"),
        ("Plans from $99/month. Book a call.", {}, "low_ticket"),
        ("Book a call.", {"domain": "github.com"}, "platform"),
        ("Book a call.", {"domain": "clutch.co"}, "platform"),
    ]
    for evidence, extra, code in cases:
        scored = _score(evidence, **extra)
        assert scored.rejection == code and scored.total == 0 and scored.level == "", evidence
    assert strong_non_us("Our office is in Ohio.", schema="US") is False
    assert strong_non_us("Our office is in Ohio.") is False
    absent = _score("A marketing agency with no place named.")
    assert absent.rejection == "" and absent.level == "C" and "us" in absent.gaps
