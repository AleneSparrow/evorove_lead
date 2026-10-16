import io
import json
from urllib.error import URLError
import pytest
from evorove_lead.research_config import AnthropicResearchCompletion
from evorove_lead.market_intelligence import ResearchUnavailable


def test_real_adapter_request_is_bounded_and_usage_preserved(monkeypatch):
    calls = []
    def open_(request, timeout):
        calls.append(json.loads(request.data))
        assert timeout == 45
        return io.BytesIO(json.dumps({"stop_reason":"end_turn", "content":[{"type":"text","text":"{}"}],
                                     "usage":{"input_tokens":21,"output_tokens":2}}).encode())
    monkeypatch.setattr("evorove_lead.research_config.urlopen", open_)
    response = AnthropicResearchCompletion("synthetic-test-key", "configured-model").complete_bounded(
        system="untrusted source handling", user="source", max_tokens=123)
    assert response.input_tokens == 21 and response.output_tokens == 2
    assert calls[0]["max_tokens"] == 123 and calls[0]["model"] == "configured-model"


def test_transport_failure_has_no_implicit_paid_retry_or_secret_leak(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise URLError("sensitive provider details")
    monkeypatch.setattr("evorove_lead.research_config.urlopen", fail)
    with pytest.raises(ResearchUnavailable, match="^research completion unavailable$"):
        AnthropicResearchCompletion("synthetic-test-key", "configured-model").complete_bounded(system="s", user="u", max_tokens=10)
    assert len(calls) == 1


def test_configured_timeout_is_forwarded_without_retry(monkeypatch):
    observed = []
    def fail(*args, **kwargs):
        observed.append(kwargs["timeout"])
        raise TimeoutError()
    monkeypatch.setattr("evorove_lead.research_config.urlopen", fail)
    with pytest.raises(ResearchUnavailable):
        AnthropicResearchCompletion("synthetic-test-key", "configured-model", timeout_seconds=90).complete_bounded(
            system="s", user="u", max_tokens=4000)
    assert observed == [90]


@pytest.mark.parametrize("timeout", [0, 4, 121])
def test_timeout_configuration_is_bounded(timeout):
    with pytest.raises(ValueError, match="between 5 and 120"):
        AnthropicResearchCompletion("synthetic-test-key", "configured-model", timeout_seconds=timeout)


@pytest.mark.parametrize("body", [
    {"stop_reason":"max_tokens"},
    {"stop_reason":"end_turn","usage":{}},
])
def test_truncated_or_unaccounted_response_is_rejected(monkeypatch, body):
    monkeypatch.setattr("evorove_lead.research_config.urlopen", lambda *a, **kw: io.BytesIO(json.dumps(body).encode()))
    with pytest.raises(ResearchUnavailable):
        AnthropicResearchCompletion("synthetic-test-key", "configured-model").complete_bounded(system="s", user="u", max_tokens=10)
