import io
import json
from urllib.error import HTTPError, URLError
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from evorove_lead.research_config import (
    AnthropicResearchCompletion,
    configured_research_effort,
    research_pipeline_from_env,
    require_research_effort,
    safe_truncation_metrics,
)
from evorove_lead.market_intelligence import ResearchUnavailable, TruncatedStructuredCompletion
from evorove_lead.research_budget import BudgetLimits, ResearchBudget
from evorove_lead.llm_usage import BudgetedCompletion, LLMUsageLedger, UsageRow
from evorove_lead.sqlalchemy_models import Base


def test_real_adapter_request_is_bounded_and_usage_preserved(monkeypatch):
    calls = []
    def open_(request, timeout):
        calls.append((request, json.loads(request.data)))
        assert timeout == 90
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
    assert payload["output_config"] == {"effort": "low"}
    assert "temperature" not in payload and "thinking" not in payload
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-api-key") == "synthetic-test-key"


def test_complete_bounded_forwards_override_effort(monkeypatch):
    calls = []
    def open_(request, timeout):
        calls.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"stop_reason":"end_turn", "content":[{"type":"text","text":"{}"}],
                                     "usage":{"input_tokens":1,"output_tokens":1}}).encode())
    monkeypatch.setattr("evorove_lead.research_config.urlopen", open_)
    AnthropicResearchCompletion("synthetic-test-key", "claude-sonnet-5-5", effort="low").complete_bounded(
        system="s", user="u", max_tokens=10, effort="medium")
    assert calls[0]["output_config"] == {"effort": "medium"}
    assert "thinking" not in calls[0]


def test_invalid_effort_is_rejected_before_network():
    with pytest.raises(ValueError, match="must be one of: low, medium, high"):
        AnthropicResearchCompletion("synthetic-test-key", "claude-sonnet-5-5", effort="disabled")
    with pytest.raises(ValueError, match="must be one of"):
        require_research_effort("xhigh", name="RESEARCH_EFFORT")


def test_configured_effort_defaults_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("RESEARCH_EFFORT", raising=False)
    assert configured_research_effort("RESEARCH_EFFORT", "low") == "low"
    monkeypatch.setenv("RESEARCH_EFFORT", "")
    assert configured_research_effort("RESEARCH_EFFORT", "low") == "low"
    monkeypatch.setenv("RESEARCH_EFFORT", "high")
    assert configured_research_effort("RESEARCH_EFFORT", "low") == "high"
    monkeypatch.setenv("RESEARCH_EFFORT", "disabled")
    with pytest.raises(ResearchUnavailable, match="RESEARCH_EFFORT must be one of"):
        configured_research_effort("RESEARCH_EFFORT", "low")


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


def _research_env(monkeypatch):
    monkeypatch.setenv("RESEARCH_ENABLED", "1")
    monkeypatch.setenv("RESEARCH_ALLOW_PAID_LLM", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-unused")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("RESEARCH_INPUT_USD_PER_MTOK", "3")
    monkeypatch.setenv("RESEARCH_OUTPUT_USD_PER_MTOK", "15")
    monkeypatch.setenv("RESEARCH_BUDGET_JSON", '{"queries":10,"pages":60,"cost_microusd":1000000,"daily_cost_microusd":1000000}')


def test_explicit_budget_json_and_tariffs_are_accepted(monkeypatch):
    _research_env(monkeypatch)
    class Warehouse:
        database_engine = object()
    pipeline = research_pipeline_from_env(Warehouse(), object(), dry_run=True)
    assert pipeline is not None
    assert pipeline.model == "claude-sonnet-5-5"
    assert pipeline.max_output_tokens == 4000
    assert pipeline.effort == "low"
    assert pipeline.qualify_effort == "medium"
    assert pipeline.completion.timeout_seconds == 90
    assert pipeline.completion.effort == "low"
    assert pipeline.limits == BudgetLimits(queries=10, pages=60, cost_microusd=1_000_000, daily_cost_microusd=1_000_000)
    assert pipeline.input_rate == 3
    assert pipeline.output_rate == 15
    assert pipeline.dry_run is True


def test_from_env_rejects_invalid_effort_before_any_call(monkeypatch):
    _research_env(monkeypatch)
    monkeypatch.setenv("RESEARCH_EFFORT", "disabled")
    class Warehouse:
        database_engine = object()
    with pytest.raises(ResearchUnavailable, match="RESEARCH_EFFORT must be one of"):
        research_pipeline_from_env(Warehouse(), object(), dry_run=True)
    monkeypatch.setenv("RESEARCH_EFFORT", "low")
    monkeypatch.setenv("RESEARCH_EFFORT_QUALIFY", "adaptive")
    with pytest.raises(ResearchUnavailable, match="RESEARCH_EFFORT_QUALIFY must be one of"):
        research_pipeline_from_env(Warehouse(), object(), dry_run=True)


def test_from_env_forwards_explicit_effort_and_timeout(monkeypatch):
    _research_env(monkeypatch)
    monkeypatch.setenv("RESEARCH_EFFORT", "medium")
    monkeypatch.setenv("RESEARCH_EFFORT_QUALIFY", "high")
    monkeypatch.setenv("RESEARCH_MAX_OUTPUT_TOKENS", "4000")
    monkeypatch.setenv("RESEARCH_LLM_TIMEOUT_SECONDS", "90")
    class Warehouse:
        database_engine = object()
    pipeline = research_pipeline_from_env(Warehouse(), object(), dry_run=True)
    assert pipeline.effort == "medium"
    assert pipeline.qualify_effort == "high"
    assert pipeline.max_output_tokens == 4000
    assert pipeline.completion.timeout_seconds == 90


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


def test_truncated_response_records_safe_metrics_without_bodies(monkeypatch):
    body = {
        "stop_reason": "max_tokens",
        "content": [
            {"type": "thinking", "thinking": "SECRET_REASONING_MUST_NOT_STORE"},
            {"type": "text", "text": '{"prompt":"SECRET_JSON"}'},
        ],
        "usage": {
            "input_tokens": 4852,
            "output_tokens": 4000,
            "output_tokens_details": {"thinking_tokens": 2100},
        },
    }
    monkeypatch.setattr("evorove_lead.research_config.urlopen", lambda *a, **kw: io.BytesIO(json.dumps(body).encode()))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    ledger = LLMUsageLedger(engine)
    budget = ResearchBudget("a", BudgetLimits(llm_calls=2, input_tokens=100_000, output_tokens=10_000, cost_microusd=10**9))
    completion = AnthropicResearchCompletion("synthetic-test-key", "claude-sonnet-5-5")
    billed = BudgetedCompletion(completion, budget, ledger, "run-trunc", max_output_tokens=4000, model="claude-sonnet-5-5")
    with pytest.raises(TruncatedStructuredCompletion, match=r"^truncated structured completion$"):
        billed.complete(system="SECRET_SYSTEM_PROMPT", user="SECRET_USER_BODY")
    with Session(engine) as session:
        rows = [row.payload for row in session.scalars(select(UsageRow).order_by(UsageRow.recorded_at))]
    assert [row["status"] for row in rows] == ["reserved", "truncated"]
    metrics = rows[1]
    assert metrics["stop_reason"] == "max_tokens"
    assert metrics["input_tokens"] == 4852
    assert metrics["output_tokens"] == 4000
    assert metrics["thinking_tokens"] == 2100
    assert metrics["text_chars"] == len('{"prompt":"SECRET_JSON"}')
    assert metrics["content_block_types"] == {"thinking": 1, "text": 1}
    dumped = json.dumps(rows)
    assert "SECRET" not in dumped
    assert "synthetic-test-key" not in dumped


def test_safe_truncation_metrics_drop_unsafe_fields():
    metrics = safe_truncation_metrics({
        "stop_reason": "max_tokens; drop this",
        "content": [{"type": "text with space", "text": "abc"}, {"type": "text", "text": "xyz"}],
        "usage": {"input_tokens": 3, "output_tokens": "nope"},
    })
    assert metrics["stop_reason"] == "max_tokens"
    assert metrics["input_tokens"] == 3
    assert metrics["output_tokens"] is None
    assert metrics["thinking_tokens"] is None
    assert metrics["text_chars"] == 3
    assert metrics["content_block_types"] == {"text": 1}
