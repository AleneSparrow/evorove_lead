import io
import json
from urllib.error import HTTPError, URLError
import pytest
from evorove_lead.research_config import AnthropicResearchCompletion, research_pipeline_from_env
from evorove_lead.market_intelligence import ResearchUnavailable
from evorove_lead.research_budget import BudgetLimits


def test_real_adapter_request_is_bounded_and_usage_preserved(monkeypatch):
    calls = []
    def open_(request, timeout):
        calls.append((request, json.loads(request.data)))
        assert timeout == 45
        return io.BytesIO(json.dumps({"stop_reason":"end_turn", "content":[{"type":"text","text":"{}"}],
                                     "usage":{"input_tokens":21,"output_tokens":2}}).encode())
    monkeypatch.setattr("evorove_lead.research_config.urlopen", open_)
    response = AnthropicResearchCompletion("synthetic-test-key", "claude-sonnet-5-5").complete_bounded(
        system="untrusted source handling", user="source", max_tokens=123)
    assert response.input_tokens == 21 and response.output_tokens == 2
    request, payload = calls[0]
    assert payload["max_tokens"] == 123 and payload["model"] == "claude-sonnet-5-5"
    assert payload["system"] == "untrusted source handling"
    assert payload["messages"] == [{"role": "user", "content": "source"}]
    assert "temperature" not in payload and "thinking" not in payload
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-api-key") == "synthetic-test-key"


def test_transport_failure_has_no_implicit_paid_retry_or_secret_leak(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise URLError("sensitive provider details")
    monkeypatch.setattr("evorove_lead.research_config.urlopen", fail)
    with pytest.raises(ResearchUnavailable, match=r"^research completion unavailable: URLError$") as raised:
        AnthropicResearchCompletion("synthetic-test-key", "configured-model").complete_bounded(system="s", user="u", max_tokens=10)
    assert "sensitive" not in str(raised.value)
    assert "synthetic-test-key" not in str(raised.value)
    assert len(calls) == 1


def test_http_error_keeps_status_and_provider_type_without_bodies(monkeypatch):
    def fail(request, timeout):
        body = json.dumps({
            "type": "error",
            "error": {
                "type": "not_found_error",
                "message": "model secret-should-not-leak and request echo",
            },
        }).encode()
        raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=io.BytesIO(body))
    monkeypatch.setattr("evorove_lead.research_config.urlopen", fail)
    with pytest.raises(ResearchUnavailable, match=r"^research completion unavailable: HTTP 404 not_found_error$") as raised:
        AnthropicResearchCompletion("synthetic-test-key", "claude-sonnet-5-5").complete_bounded(system="s", user="u", max_tokens=10)
    text = str(raised.value)
    assert "secret-should-not-leak" not in text
    assert "synthetic-test-key" not in text
    assert "request echo" not in text


def test_explicit_budget_json_and_tariffs_are_accepted(monkeypatch):
    monkeypatch.setenv("RESEARCH_ENABLED", "1")
    monkeypatch.setenv("RESEARCH_ALLOW_PAID_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-unused")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("RESEARCH_INPUT_USD_PER_MTOK", "3")
    monkeypatch.setenv("RESEARCH_OUTPUT_USD_PER_MTOK", "15")
    monkeypatch.setenv("RESEARCH_BUDGET_JSON", '{"queries":10,"pages":60,"cost_microusd":1000000,"daily_cost_microusd":1000000}')
    class Warehouse:
        database_engine = object()
    pipeline = research_pipeline_from_env(Warehouse(), object(), dry_run=True)
    assert pipeline is not None
    assert pipeline.model == "claude-sonnet-5-5"
    assert pipeline.max_output_tokens == 2000
    assert pipeline.limits == BudgetLimits(queries=10, pages=60, cost_microusd=1_000_000, daily_cost_microusd=1_000_000)
    assert pipeline.input_rate == 3
    assert pipeline.output_rate == 15
    assert pipeline.dry_run is True


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
