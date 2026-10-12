"""Wire classification into the engine without a second pipeline.

``pipeline_from_env`` stays off unless ``AI_PROVIDER=anthropic``. An unknown
model price raises before any request, so a misconfigured model cannot run
as a free call.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from evorove_lead.cost_ledger import (
    DEFAULT_MAX_BUSINESS_MICROS,
    DEFAULT_MAX_CALLS_PER_RUN,
    DEFAULT_MAX_GLOBAL_MICROS,
    DEFAULT_MAX_RUN_MICROS,
    MODEL_PRICES_MICROS_PER_MTOK,
    RECOMMENDED_MODEL,
    CostLedger,
)
from evorove_lead.semantic_classify import AnthropicCompletion, Classification, classify_excerpt
from evorove_lead.warehouse import LlmUsageRecord, QualificationRecord, new_id


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return int(raw)


def _usd_env_to_micros(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return int(round(float(raw) * 1_000_000))


class LeadPipeline:
    def __init__(self, completion, ledger: CostLedger, *, reasoning=None, cache: dict | None = None) -> None:
        self._completion = completion
        self._ledger = ledger
        self._reasoning = reasoning
        self._cache = cache if cache is not None else {}

    @property
    def ledger(self) -> CostLedger:
        return self._ledger

    def qualify(
        self,
        *,
        service: str,
        audiences: tuple[str, ...],
        source_url: str,
        source_text: str,
        query_template: str,
        publication_date: str | None = None,
        author: str | None = None,
        geo_hint: str = "",
    ) -> Classification:
        return classify_excerpt(
            service=service,
            audiences=audiences,
            source_url=source_url,
            source_text=source_text,
            query_template=query_template,
            publication_date=publication_date,
            author=author,
            geo_hint=geo_hint,
            completion=self._completion,
            ledger=self._ledger,
            cache=self._cache,
            reasoning=self._reasoning,
        )


def record_from_classification(
    classification: Classification,
    *,
    business_id: str,
    hypothesis_id: str,
    trace_id: str,
    now: datetime | None = None,
) -> QualificationRecord:
    return QualificationRecord(
        id=new_id("qualification"),
        business_id=business_id,
        hypothesis_id=hypothesis_id,
        trace_id=trace_id,
        source_url=classification.source_url,
        content_sha256=classification.content_sha256,
        query_template=classification.query_template,
        segment=classification.segment,
        intent_class=classification.intent_class,
        entity_type=classification.entity_type,
        problem=classification.problem or "",
        icp_match=classification.icp_match,
        evidence_quote=classification.evidence_quote or "",
        evidence_verified=classification.evidence_verified,
        publication_date=classification.publication_date or "",
        confidence=classification.confidence,
        commercial_relevance=classification.commercial_relevance,
        recommended_action=classification.recommended_action,
        reasoning_summary=classification.reasoning_summary,
        model=classification.model,
        input_tokens=classification.input_tokens,
        output_tokens=classification.output_tokens,
        cost_usd_micros=classification.cost_usd_micros,
        cache_hit=classification.cache_hit,
        blocked_reason=classification.blocked_reason,
        created_at=now or datetime.now(timezone.utc),
    )


def usage_from_classification(
    classification: Classification, *, business_id: str, now: datetime | None = None
) -> LlmUsageRecord | None:
    if classification.cache_hit:
        return None
    if classification.input_tokens <= 0 and not classification.blocked_reason:
        return None
    return LlmUsageRecord(
        id=new_id("usage"),
        business_id=business_id,
        model=classification.model,
        input_tokens=classification.input_tokens,
        output_tokens=classification.output_tokens,
        cost_usd_micros=classification.cost_usd_micros,
        cache_hit=False,
        blocked_reason=classification.blocked_reason,
        created_at=now or datetime.now(timezone.utc),
    )


def pipeline_from_env(warehouse=None) -> LeadPipeline | None:
    """None when the owner left the provider deterministic.

    ``AI_PROVIDER=anthropic`` requires a key, a model, and a known price.
    The same ``ANTHROPIC_MODEL`` is used for classification. The current
    Sonnet id is ``claude-sonnet-5-5``.
    """

    provider = (os.getenv("AI_PROVIDER") or "deterministic").strip()
    if provider == "deterministic":
        return None
    if provider != "anthropic":
        raise RuntimeError("AI_PROVIDER must be 'deterministic' or 'anthropic'")
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    model = (os.getenv("ANTHROPIC_MODEL") or "").strip()
    if not api_key or not model:
        raise RuntimeError(
            "ANTHROPIC_API_KEY and ANTHROPIC_MODEL are required when AI_PROVIDER=anthropic"
        )
    if model not in MODEL_PRICES_MICROS_PER_MTOK:
        raise RuntimeError(
            "unknown model price blocks paid execution for "
            f"{model!r}; set ANTHROPIC_MODEL to {RECOMMENDED_MODEL!r} "
            "or another priced Sonnet id"
        )
    prior_business = 0
    prior_global = 0
    if warehouse is not None and hasattr(warehouse, "spent_micros"):
        # Global spend is known here. Tenant spend is applied per run by
        # the engine, once the business id exists. Copying the global
        # total into the tenant cap would stop a business that has not
        # spent anything.
        prior_global = int(warehouse.spent_micros(None))
    ledger = CostLedger(
        model,
        max_calls=_int_env("LEAD_MAX_CLASSIFICATIONS_PER_RUN", DEFAULT_MAX_CALLS_PER_RUN),
        max_run_micros=_usd_env_to_micros("LEAD_MAX_USD_PER_RUN", DEFAULT_MAX_RUN_MICROS),
        max_business_micros=_usd_env_to_micros("LEAD_MAX_USD_PER_BUSINESS", DEFAULT_MAX_BUSINESS_MICROS),
        max_global_micros=_usd_env_to_micros("LEAD_MAX_USD_GLOBAL", DEFAULT_MAX_GLOBAL_MICROS),
        prior_business_micros=prior_business,
        prior_global_micros=prior_global,
    )
    return LeadPipeline(AnthropicCompletion(api_key, model), ledger)


def cost_summary(records: list[QualificationRecord]) -> dict[str, int]:
    opportunities = [row for row in records if row.segment == "opportunity"]
    total = sum(row.cost_usd_micros for row in records)
    return {
        "classifications": len(records),
        "cache_hits": sum(1 for row in records if row.cache_hit),
        "input_tokens": sum(row.input_tokens for row in records),
        "output_tokens": sum(row.output_tokens for row in records),
        "cost_usd_micros": total,
        "opportunities": len(opportunities),
        "prospects": sum(1 for row in records if row.segment == "prospect"),
        "rejected": sum(1 for row in records if row.segment == "rejected"),
        "review": sum(1 for row in records if row.segment == "review"),
        "verified_evidence": sum(1 for row in records if row.evidence_verified),
        "cost_per_opportunity_micros": (total // len(opportunities)) if opportunities else 0,
    }


def precision_at_k(flags: list[bool], k: int) -> float | None:
    """Share of the first k predictions that the caller labeled relevant.

    This does not estimate recall, and a fixture score is not a field result.
    """

    if k <= 0 or not flags:
        return None
    window = flags[:k]
    return sum(1 for item in window if item) / len(window)
