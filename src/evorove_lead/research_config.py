"""Opt-in research wiring. Never loads .env; no call is made while constructing."""
from __future__ import annotations
import json
import os
from urllib.request import Request, urlopen
from evorove_lead.llm_usage import CompletionReply
from evorove_lead.market_intelligence import ResearchUnavailable
from evorove_lead.research_budget import BudgetLimits

class AnthropicResearchCompletion:
    def __init__(self, api_key, model, *, timeout_seconds=45):
        if not 5 <= timeout_seconds <= 120:
            raise ValueError("research LLM timeout must be between 5 and 120 seconds")
        self._api_key, self.model = api_key, model
        self.timeout_seconds = timeout_seconds
    def complete_bounded(self, *, system, user, max_tokens):
        request = Request("https://api.anthropic.com/v1/messages", data=json.dumps({"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}).encode(), headers={"content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": self._api_key}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read(2_000_000))
        except Exception:
            raise ResearchUnavailable("research completion unavailable") from None
        if body.get("stop_reason") == "max_tokens": raise ResearchUnavailable("truncated structured completion")
        usage = body.get("usage", {})
        if not isinstance(usage.get("input_tokens"), int) or not isinstance(usage.get("output_tokens"), int):
            raise ResearchUnavailable("provider usage missing")
        return CompletionReply(text="".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"), input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"])


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
    completion = AnthropicResearchCompletion(key, model, timeout_seconds=int(os.getenv("RESEARCH_LLM_TIMEOUT_SECONDS", "45")))
    return ResearchPipeline(warehouse.database_engine, search_client, completion, limits=limits,
                            dry_run=(os.getenv("RESEARCH_CRM_DELIVERY") != "1") if dry_run is None else dry_run, model=model,
                            max_output_tokens=int(os.getenv("RESEARCH_MAX_OUTPUT_TOKENS", "2000")), input_rate=float(rates[0]), output_rate=float(rates[1]))
