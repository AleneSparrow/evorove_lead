"""Opt-in research wiring. Never loads .env; no call is made while constructing."""
from __future__ import annotations
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from evorove_lead.llm_usage import CompletionReply
from evorove_lead.market_intelligence import ResearchUnavailable, TruncatedStructuredCompletion
from evorove_lead.research_budget import BudgetLimits

_SAFE_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PROVIDER_TYPE = re.compile(r"[a-z0-9_]{1,64}$")
_BLOCK_TYPE = re.compile(r"[a-z0-9_]{1,32}$")
_STOP_REASON = re.compile(r"[A-Za-z0-9_]{1,64}$")
ALLOWED_RESEARCH_EFFORT = ("low", "medium", "high")


def require_research_effort(value, *, name="effort"):
    if value not in ALLOWED_RESEARCH_EFFORT:
        raise ValueError(f"{name} must be one of: low, medium, high")
    return value


def configured_research_effort(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip()
    if value not in ALLOWED_RESEARCH_EFFORT:
        raise ResearchUnavailable(f"{name} must be one of: low, medium, high")
    return value


def _provider_error_type(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read(4000).decode() or "{}")
    except Exception:
        return ""
    err = payload.get("error") if isinstance(payload, dict) else None
    kind = err.get("type") if isinstance(err, dict) else None
    return kind if isinstance(kind, str) and _PROVIDER_TYPE.fullmatch(kind) else ""


def _safe_completion_failure(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        detail = f"HTTP {exc.code}"
        kind = _provider_error_type(exc)
        if kind:
            detail += f" {kind}"
        return f"research completion unavailable: {detail}"
    if isinstance(exc, TimeoutError):
        return "research completion unavailable: timeout"
    if isinstance(exc, URLError):
        return "research completion unavailable: URLError"
    name = type(exc).__name__
    if not _SAFE_TYPE.fullmatch(name):
        name = "error"
    return f"research completion unavailable: {name}"


def _text_from_content(body: dict) -> str:
    blocks = body.get("content") if isinstance(body, dict) else None
    if not isinstance(blocks, list):
        return ""
    return "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")


def _content_block_types(body: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    blocks = body.get("content") if isinstance(body, dict) else None
    if not isinstance(blocks, list):
        return counts
    for block in blocks:
        kind = block.get("type") if isinstance(block, dict) else None
        if isinstance(kind, str) and _BLOCK_TYPE.fullmatch(kind):
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def _thinking_tokens(body: dict) -> int | None:
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return None
    details = usage.get("output_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("thinking_tokens"), int):
        return details["thinking_tokens"]
    if isinstance(usage.get("thinking_tokens"), int):
        return usage["thinking_tokens"]
    return None


def safe_truncation_metrics(body: dict) -> dict:
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    stop = body.get("stop_reason") if isinstance(body, dict) else None
    return {
        "stop_reason": stop if isinstance(stop, str) and _STOP_REASON.fullmatch(stop) else "max_tokens",
        "input_tokens": usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else None,
        "output_tokens": usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else None,
        "thinking_tokens": _thinking_tokens(body if isinstance(body, dict) else {}),
        "text_chars": len(_text_from_content(body if isinstance(body, dict) else {})),
        "content_block_types": _content_block_types(body if isinstance(body, dict) else {}),
    }


class AnthropicResearchCompletion:
    def __init__(self, api_key, model, *, timeout_seconds=90, effort="low"):
        if not 5 <= timeout_seconds <= 120:
            raise ValueError("research LLM timeout must be between 5 and 120 seconds")
        self._api_key, self.model = api_key, model
        self.timeout_seconds = timeout_seconds
        self.effort = require_research_effort(effort, name="effort")

    def complete_bounded(self, *, system, user, max_tokens, effort=None):
        chosen = self.effort if effort is None else require_research_effort(effort, name="effort")
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": chosen},
        }
        request = Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": self._api_key},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read(2_000_000))
        except Exception as exc:
            raise ResearchUnavailable(_safe_completion_failure(exc)) from None
        if body.get("stop_reason") == "max_tokens":
            raise TruncatedStructuredCompletion(safe_truncation_metrics(body))
        usage = body.get("usage", {})
        if not isinstance(usage.get("input_tokens"), int) or not isinstance(usage.get("output_tokens"), int):
            raise ResearchUnavailable("provider usage missing")
        return CompletionReply(
            text="".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
        )


def research_pipeline_from_env(warehouse, search_client, *, dry_run=None):
    if os.getenv("RESEARCH_ENABLED") != "1": return None
    if os.getenv("RESEARCH_ALLOW_PAID_LLM") != "1":
        raise ResearchUnavailable("paid research calls require explicit RESEARCH_ALLOW_PAID_LLM authorization")
    if not hasattr(warehouse, "database_engine"):
        raise ResearchUnavailable("research requires persistent warehouse")
    key, model = os.getenv("ANTHROPIC_API_KEY", ""), os.getenv("ANTHROPIC_MODEL", "")
    if not key or not model: raise ResearchUnavailable("research model configuration unavailable")
    rates = (os.getenv("RESEARCH_INPUT_USD_PER_MTOK"), os.getenv("RESEARCH_OUTPUT_USD_PER_MTOK"))
    if not all(rates) or any(float(r) <= 0 for r in rates):
        raise ResearchUnavailable("explicit positive model tariff required for cost enforcement")
    from evorove_lead.research_runtime import ResearchPipeline
    limits = BudgetLimits.model_validate_json(os.getenv("RESEARCH_BUDGET_JSON", "{}"))
    effort = configured_research_effort("RESEARCH_EFFORT", "low")
    # low can weaken need/entailment judgment. Qualification and adversarial
    # review stay on medium so a cheaper DNA pass does not cheapen the gate.
    qualify_effort = configured_research_effort("RESEARCH_EFFORT_QUALIFY", "medium")
    completion = AnthropicResearchCompletion(
        key, model,
        timeout_seconds=int(os.getenv("RESEARCH_LLM_TIMEOUT_SECONDS", "90")),
        effort=effort,
    )
    from evorove_lead.crm_touch import crm_delivery_requested
    return ResearchPipeline(
        warehouse.database_engine, search_client, completion, limits=limits,
        dry_run=(not crm_delivery_requested()) if dry_run is None else dry_run, model=model,
        max_output_tokens=int(os.getenv("RESEARCH_MAX_OUTPUT_TOKENS", "4000")),
        input_rate=float(rates[0]), output_rate=float(rates[1]),
        effort=effort, qualify_effort=qualify_effort,
    )
