"""Module 3's LLM fallback: grounded, or dropped -- never passed through."""

import json

import pytest
import urllib.error

from evorove_lead.decision_maker_llm import (
    AnthropicDecisionMakerCompletion,
    DecisionMakerLLMUnavailable,
    DeterministicDecisionMakerCompletion,
    ScriptedDecisionMakerCompletion,
    find_decision_maker_llm,
)

PAGE = (
    "https://www.tonys-pizza.com/about",
    "Meet Jane Doe, our Founder. Reach her directly at jane@tonys-pizza.com "
    "or the shop's front desk at info@tonys-pizza.com.",
)


def _payload(**overrides):
    base = {
        "name": "Jane Doe",
        "role": "Founder",
        "email": "jane@tonys-pizza.com",
        "evidence_quote": "Meet Jane Doe, our Founder",
    }
    base.update(overrides)
    return json.dumps(base)


def test_accepts_a_grounded_answer():
    llm = ScriptedDecisionMakerCompletion(_payload())

    result = find_decision_maker_llm((PAGE,), "tonys-pizza.com", llm)

    assert result is not None
    assert result.name == "Jane Doe"
    assert result.email == "jane@tonys-pizza.com"
    assert result.source_url == PAGE[0]
    assert llm.calls == 1


def test_rejects_an_evidence_quote_not_in_the_page():
    llm = ScriptedDecisionMakerCompletion(_payload(evidence_quote="Jane Doe founded this in 1994"))

    assert find_decision_maker_llm((PAGE,), "tonys-pizza.com", llm) is None


def test_rejects_an_email_not_actually_on_the_page():
    llm = ScriptedDecisionMakerCompletion(_payload(email="jane@somewhere-else.com"))

    assert find_decision_maker_llm((PAGE,), "tonys-pizza.com", llm) is None


def test_rejects_an_email_on_a_different_domain():
    page = (PAGE[0], PAGE[1] + " Or media: jane@example-press.com.")
    llm = ScriptedDecisionMakerCompletion(_payload(email="jane@example-press.com"))

    assert find_decision_maker_llm((page,), "tonys-pizza.com", llm) is None


def test_rejects_a_generic_business_inbox():
    llm = ScriptedDecisionMakerCompletion(_payload(email="info@tonys-pizza.com"))

    assert find_decision_maker_llm((PAGE,), "tonys-pizza.com", llm) is None


def test_rejects_when_the_model_says_it_found_no_one():
    llm = ScriptedDecisionMakerCompletion(
        json.dumps({"name": None, "role": None, "email": None, "evidence_quote": None})
    )

    assert find_decision_maker_llm((PAGE,), "tonys-pizza.com", llm) is None


def test_rejects_malformed_json():
    llm = ScriptedDecisionMakerCompletion("not json")

    assert find_decision_maker_llm((PAGE,), "tonys-pizza.com", llm) is None


def test_strips_a_markdown_code_fence():
    llm = ScriptedDecisionMakerCompletion(f"```json\n{_payload()}\n```")

    result = find_decision_maker_llm((PAGE,), "tonys-pizza.com", llm)

    assert result is not None
    assert result.email == "jane@tonys-pizza.com"


def test_moves_on_to_the_next_page_after_a_page_with_no_answer():
    other_page = (
        "https://www.tonys-pizza.com/",
        "Tony's Pizza, open since 1994.",
    )
    calls: list[str] = []

    class TwoAnswerCompletion:
        def complete(self, *, system: str, user: str) -> str:
            calls.append(user)
            if len(calls) == 1:
                return json.dumps({"name": None, "role": None, "email": None, "evidence_quote": None})
            return _payload()

    result = find_decision_maker_llm((other_page, PAGE), "tonys-pizza.com", TwoAnswerCompletion())

    assert result is not None
    assert result.source_url == PAGE[0]
    assert len(calls) == 2


def test_skips_a_blank_page_without_calling_the_model():
    llm = ScriptedDecisionMakerCompletion(_payload())

    result = find_decision_maker_llm((("https://www.tonys-pizza.com/", "   "), PAGE), "tonys-pizza.com", llm)

    assert result is not None
    assert llm.calls == 1


def test_deterministic_completion_fails_closed():
    with pytest.raises(DecisionMakerLLMUnavailable):
        DeterministicDecisionMakerCompletion().complete(system="s", user="u")


def test_anthropic_http_error_does_not_include_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        del args, kwargs
        raise urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 401, "unauthorized", hdrs=None, fp=None
        )

    monkeypatch.setattr("evorove_lead.decision_maker_llm.urllib.request.urlopen", boom)
    client = AnthropicDecisionMakerCompletion(api_key="test-key-not-real", model="claude-test")

    with pytest.raises(DecisionMakerLLMUnavailable, match="HTTP 401") as raised:
        client.complete(system="s", user="u")

    assert "test-key-not-real" not in str(raised.value)
