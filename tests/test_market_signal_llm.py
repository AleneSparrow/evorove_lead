"""Track 4's LLM filter: grounded and relevant, or dropped -- never passed through."""

import json

import pytest
import urllib.error

from evorove_lead.market_signal_llm import (
    AnthropicMarketSignalCompletion,
    DeterministicMarketSignalCompletion,
    MarketSignalLLMUnavailable,
    ScriptedMarketSignalCompletion,
    evaluate_signal,
)

SNIPPET = (
    "New state licensing rules take effect next month for independent hair "
    "salons that want to hire booth renters as W-2 employees."
)


def _payload(**overrides):
    base = {
        "relevant": True,
        "segment_label": "independent hair salons converting booth renters to employees",
        "channel": "industry_sites",
        "evidence_quote": "New state licensing rules take effect next month",
    }
    base.update(overrides)
    return json.dumps(base)


def test_accepts_a_grounded_relevant_signal():
    llm = ScriptedMarketSignalCompletion(_payload())

    result = evaluate_signal(SNIPPET, "salon software", llm)

    assert result is not None
    assert result.channel == "industry_sites"
    assert "licensing rules" in result.evidence_quote
    assert llm.calls == 1


def test_rejects_when_the_model_says_not_relevant():
    llm = ScriptedMarketSignalCompletion(
        json.dumps({"relevant": False, "segment_label": None, "channel": None, "evidence_quote": None})
    )

    assert evaluate_signal(SNIPPET, "salon software", llm) is None


def test_rejects_an_evidence_quote_not_in_the_snippet():
    llm = ScriptedMarketSignalCompletion(_payload(evidence_quote="something the model made up"))

    assert evaluate_signal(SNIPPET, "salon software", llm) is None


def test_rejects_an_unknown_channel():
    llm = ScriptedMarketSignalCompletion(_payload(channel="social_media"))

    assert evaluate_signal(SNIPPET, "salon software", llm) is None


def test_rejects_malformed_json():
    llm = ScriptedMarketSignalCompletion("not json")

    assert evaluate_signal(SNIPPET, "salon software", llm) is None


def test_strips_a_markdown_code_fence():
    llm = ScriptedMarketSignalCompletion(f"```json\n{_payload()}\n```")

    result = evaluate_signal(SNIPPET, "salon software", llm)

    assert result is not None


def test_rejects_an_empty_snippet_without_calling_the_model():
    llm = ScriptedMarketSignalCompletion(_payload())

    assert evaluate_signal("   ", "salon software", llm) is None
    assert llm.calls == 0


def test_deterministic_completion_fails_closed():
    with pytest.raises(MarketSignalLLMUnavailable):
        DeterministicMarketSignalCompletion().complete(system="s", user="u")


def test_anthropic_http_error_does_not_include_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        del args, kwargs
        raise urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 401, "unauthorized", hdrs=None, fp=None
        )

    monkeypatch.setattr("evorove_lead.market_signal_llm.urllib.request.urlopen", boom)
    client = AnthropicMarketSignalCompletion(api_key="test-key-not-real", model="claude-test")

    with pytest.raises(MarketSignalLLMUnavailable, match="HTTP 401") as raised:
        client.complete(system="s", user="u")

    assert "test-key-not-real" not in str(raised.value)
