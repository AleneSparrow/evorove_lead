"""Marketing analysis keeps an inference only when its quote is in the materials."""

from __future__ import annotations

import json
import urllib.error

import pytest

from evorove_lead.business import BusinessSeed
from evorove_lead.engine import GenerationStatus, LeadGenerationEngine
from evorove_lead.hypothesis import GeoRadius, Hypothesis, IntentTrigger
from evorove_lead.marketing_analysis import (
    AnthropicMarketingCompletion,
    DeterministicMarketingCompletion,
    MarketingAnalysisRejected,
    MarketingAnalyzer,
    ScriptedMarketingCompletion,
    marketing_analyzer_from_env,
)
from evorove_lead.materials import DepositedMaterial
from evorove_lead.warehouse import RecordingAnalysisWarehouse

BODY = (
    "We sell a living sales board for the owner's own pipeline.\n"
    "Serves independent salons and helps local repair shops.\n"
    "Built for owners who already pay for software.\n"
)
MATERIAL = DepositedMaterial(name="service.txt", body=BODY)


def _segment(label: str, quote: str, channel: str = "business_listing", source: str = "service.txt") -> dict:
    return {
        "label": label,
        "evidence_quote": quote,
        "source_name": source,
        "channel": channel,
    }


def _payload(*segments: dict, price: dict | None = None) -> str:
    return json.dumps(
        {
            "product": {"quote": "living sales board", "source_name": "service.txt"},
            "price": price,
            "place": None,
            "promotion": None,
            "segments": list(segments),
        }
    )


def _two_segments() -> str:
    return _payload(
        _segment("owners who already pay for software", "owners who already pay for software"),
        _segment(
            "small businesses that want a living sales board",
            "living sales board",
            channel="forums",
        ),
    )


def test_inferred_label_is_kept_when_its_quote_is_in_the_material() -> None:
    analysis = MarketingAnalyzer(ScriptedMarketingCompletion(_two_segments())).analyze((MATERIAL,))

    assert analysis.product is not None
    assert analysis.product.quote == "living sales board"
    assert [segment.label for segment in analysis.segments] == [
        "owners who already pay for software",
        "small businesses that want a living sales board",
    ]
    assert analysis.segments[0].evidence_quote == "owners who already pay for software"


def test_ungrounded_quote_and_invented_price_are_dropped() -> None:
    raw = _payload(
        _segment("owners who already pay for software", "owners who already pay for software"),
        _segment("salon owners", "this quote is not in the file", channel="news"),
        _segment("local shop owners who pay for software", "already pay for software", channel="forums"),
        price={"quote": "$99 a month", "source_name": "service.txt"},
    )

    analysis = MarketingAnalyzer(ScriptedMarketingCompletion(raw)).analyze((MATERIAL,))

    assert analysis.price is None
    assert [segment.label for segment in analysis.segments] == [
        "owners who already pay for software",
        "local shop owners who pay for software",
    ]


def test_fewer_than_two_grounded_segments_rejects_the_reading() -> None:
    raw = _payload(
        _segment("owners who already pay for software", "owners who already pay for software"),
        _segment("salon owners", "not in the file", channel="news"),
    )

    with pytest.raises(MarketingAnalysisRejected, match="fewer than 2"):
        MarketingAnalyzer(ScriptedMarketingCompletion(raw)).analyze((MATERIAL,))


def test_a_fourth_grounded_segment_is_not_kept() -> None:
    raw = _payload(
        _segment("owners who already pay for software", "owners who already pay for software"),
        _segment("buyers of a living sales board", "living sales board", channel="forums"),
        _segment("people who already pay for software", "already pay for software", channel="news"),
        _segment("businesses with a living sales board", "a living sales board", channel="industry_sites"),
    )

    analysis = MarketingAnalyzer(ScriptedMarketingCompletion(raw)).analyze((MATERIAL,))

    assert len(analysis.segments) == 3
    assert analysis.segments[-1].label == "people who already pay for software"


def test_label_that_invents_a_price_is_dropped() -> None:
    raw = _payload(
        _segment("owners who already pay for software", "owners who already pay for software"),
        _segment("owners offered a $99 discount", "owners who already pay for software", channel="forums"),
        _segment("buyers of a living sales board", "living sales board", channel="news"),
    )

    analysis = MarketingAnalyzer(ScriptedMarketingCompletion(raw)).analyze((MATERIAL,))

    assert "owners offered a $99 discount" not in [segment.label for segment in analysis.segments]


def test_fenced_json_is_accepted() -> None:
    fenced = "```json\n" + _two_segments() + "\n```"
    analysis = MarketingAnalyzer(ScriptedMarketingCompletion(fenced)).analyze((MATERIAL,))
    assert len(analysis.segments) == 2


def test_deterministic_completion_does_not_invent_an_audience() -> None:
    with pytest.raises(MarketingAnalysisRejected, match="does not infer"):
        DeterministicMarketingCompletion().complete(system="s", user=BODY)


def test_unset_provider_does_not_build_an_anthropic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert marketing_analyzer_from_env() is None


def test_anthropic_provider_requires_a_key_and_does_not_echo_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY") as missing:
        marketing_analyzer_from_env()
    assert "test-key-not-real" not in str(missing.value)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    analyzer = marketing_analyzer_from_env()
    assert analyzer is not None
    assert "test-key-not-real" not in repr(analyzer._completion)


def test_anthropic_http_error_does_not_include_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        del args, kwargs
        raise urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 401, "unauthorized", hdrs=None, fp=None
        )

    monkeypatch.setattr("evorove_lead.marketing_analysis.urllib.request.urlopen", boom)
    client = AnthropicMarketingCompletion(api_key="test-key-not-real", model="claude-test")

    with pytest.raises(MarketingAnalysisRejected, match="HTTP 401") as raised:
        client.complete(system="s", user="u")

    assert "test-key-not-real" not in str(raised.value)


def test_ai_inferred_hypothesis_requires_an_evidence_quote() -> None:
    with pytest.raises(ValueError, match="evidence quote"):
        Hypothesis(
            audience_segment="owners who already pay for software",
            channel="business_listing",
            query_template="owners who already pay for software in the US",
            intent_trigger=IntentTrigger(kind="business_listing", description="companies"),
            geo_radius=GeoRadius(),
            audience_source="ai_inferred",
            evidence_quote="  ",
        )


class _RecordingSearch:
    connected = True

    def __init__(self) -> None:
        self.segments: list[str] = []
        self.queries: list[str] = []
        self.sources: list[str] = []

    def find(self, hypothesis):
        self.segments.append(hypothesis.audience_segment)
        self.queries.append(hypothesis.query_template)
        self.sources.append(hypothesis.audience_source)
        return ()


class _Materials:
    def load(self, seed: BusinessSeed):
        del seed
        return (MATERIAL,)


def test_engine_searches_inferred_audiences_and_keeps_the_literal_quote() -> None:
    search = _RecordingSearch()
    warehouse = RecordingAnalysisWarehouse()
    engine = LeadGenerationEngine(
        presence=_Materials(),
        hypothesis_search=search,
        warehouse=warehouse,
        marketing_analyzer=MarketingAnalyzer(ScriptedMarketingCompletion(_two_segments())),
    )

    result = engine.generate(BusinessSeed(site_url="https://evorove.com", business_id="client-0"))

    assert result.status is GenerationStatus.NO_FIT
    assert result.sent_messages == ()
    assert "independent salons" not in search.segments
    assert "local repair shops" not in search.segments
    assert "owners who already pay for software" in search.segments
    assert set(search.sources) == {"ai_inferred"}
    assert all("for independent salons" not in query for query in search.queries)
    literal = {item["text"] for item in warehouse.briefs[0].who_may_fit}
    assert "independent salons" in literal
    stored = warehouse.briefs[0].marketing_analysis
    assert stored is not None
    assert stored["segments"][0]["label"] == "owners who already pay for software"
    assert warehouse.hypotheses[0].audience_source == "ai_inferred"
    assert warehouse.hypotheses[0].evidence_quote == "owners who already pay for software"


def test_failed_analysis_does_not_fall_back_to_copied_audiences() -> None:
    search = _RecordingSearch()
    warehouse = RecordingAnalysisWarehouse()
    engine = LeadGenerationEngine(
        presence=_Materials(),
        hypothesis_search=search,
        warehouse=warehouse,
        marketing_analyzer=MarketingAnalyzer(ScriptedMarketingCompletion("not json")),
    )

    result = engine.generate(BusinessSeed(site_url="https://evorove.com", business_id="client-0"))

    assert result.status is GenerationStatus.OFFER_INCOMPLETE
    assert search.queries == []
    assert warehouse.hypotheses == []
