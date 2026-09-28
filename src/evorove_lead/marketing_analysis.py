"""Marketing reading of a business: 4P facts plus 2–3 inferred audiences.

Separate from `offer_reader.py`. That module still copies what the business
says it sells. This one may infer who would buy it, but every inference
keeps a verbatim evidence quote, checked with the same `_ground` rule as
a price claim. A quote that is not in the named material is dropped.
A price, discount, guarantee, or legal claim that is not in the materials
is never stored.

The default provider does not call a network. `AI_PROVIDER=anthropic` is
the live reader; the owner sets `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`
herself. This module never reads a `.env` file and never logs the key.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, Sequence

from evorove_lead.hypothesis import (
    GeoRadius,
    Hypothesis,
    IntentTrigger,
    _prioritize_by_pattern_library,
)
from evorove_lead.materials import DepositedMaterial
from evorove_lead.offer import OfferRejected, OfferUnderstanding, _ground
from evorove_lead.pattern_library import PatternLibrary

SEARCH_CHANNELS = ("forums", "industry_sites", "news", "business_listing")
_MIN_SEGMENTS = 2
_MAX_SEGMENTS = 3
_MAX_LABEL_CHARS = 255
_MAX_QUOTE_CHARS = 800
_INVENTED_COMMERCIAL = re.compile(
    r"\$\d|\b(?:discount|guarantee|warranty|refund)\b",
    re.IGNORECASE,
)

_SYSTEM = """You analyze a US small business from its own public materials.
Return one JSON object and nothing else.

The object has:
- product, price, place, promotion: each is either null or {"quote": "<verbatim substring of one source>", "source_name": "<exact source name>"}.
  Use null when the materials do not state that item. Never invent a price, discount, guarantee, or legal claim.
- segments: an array of 2 or 3 objects. Each object is
  {"label": "<who would buy this offer>", "evidence_quote": "<verbatim substring that led you to the label>", "source_name": "<exact source name>", "channel": "forums" | "industry_sites" | "news" | "business_listing"}.

The label is your inference of the buyer of THIS offer. It does not have to appear verbatim.
evidence_quote and every 4P quote must be copied verbatim from the named source.
When the materials distinguish the buyer from that buyer's own customers, label the buyer.
channel is where that buyer can be found on the public web.
"""


class MarketingAnalysisRejected(ValueError):
    """The model output is not a grounded marketing reading."""


@dataclass(frozen=True)
class QuotedFact:
    """A 4P statement copied from one named material."""

    quote: str
    source_name: str


@dataclass(frozen=True)
class AudienceSegment:
    """An inferred buyer, plus the quote that prompted the label."""

    label: str
    evidence_quote: str
    source_name: str
    channel: str


@dataclass(frozen=True)
class MarketingAnalysis:
    product: QuotedFact | None
    price: QuotedFact | None
    place: QuotedFact | None
    promotion: QuotedFact | None
    segments: tuple[AudienceSegment, ...]


class MarketingCompletion(Protocol):
    def complete(self, *, system: str, user: str) -> str:
        """Return the model's raw text. Must not include an API key."""


class ScriptedMarketingCompletion:
    """A fixed reply for tests. Never opens a socket."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        del system, user
        return self._response


class DeterministicMarketingCompletion:
    """Local fallback. Does not infer an audience and does not call a network.

    Wiring this into the engine fails the analysis closed. Tests that need
    a finished reading use `ScriptedMarketingCompletion`.
    """

    def complete(self, *, system: str, user: str) -> str:
        del system, user
        raise MarketingAnalysisRejected("deterministic provider does not infer an audience")


class AnthropicMarketingCompletion:
    """HTTPS call to Anthropic. The key stays on the request header."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def __repr__(self) -> str:
        return f"AnthropicMarketingCompletion(model={self._model!r})"

    def complete(self, *, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "max_tokens": 2000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MarketingAnalysisRejected(
                f"anthropic request failed: HTTP {exc.code}"
            ) from None
        except urllib.error.URLError:
            raise MarketingAnalysisRejected("anthropic request failed") from None
        parts = [
            block.get("text", "")
            for block in body.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)


class MarketingAnalyzer:
    def __init__(self, completion: MarketingCompletion) -> None:
        self._completion = completion

    def analyze(self, materials: Sequence[DepositedMaterial]) -> MarketingAnalysis:
        if not materials:
            raise MarketingAnalysisRejected("business materials are required")
        raw = self._completion.complete(system=_SYSTEM, user=_user_prompt(materials))
        return accept_marketing_analysis(materials, _parse_json(raw))


def marketing_analyzer_from_env() -> MarketingAnalyzer | None:
    """Live analyzer only when the owner set `AI_PROVIDER=anthropic`.

    Unset or `deterministic` returns None, so the engine keeps the literal
    audience path and tests never call Anthropic. A missing key is an error,
    not a silent fallback.
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
    return MarketingAnalyzer(AnthropicMarketingCompletion(api_key=api_key, model=model))


def accept_marketing_analysis(
    materials: Sequence[DepositedMaterial], payload: dict
) -> MarketingAnalysis:
    """Keep grounded quotes and 2–3 inferred segments. Drop the rest."""

    by_name = {item.name: item for item in materials}
    segments: list[AudienceSegment] = []
    for item in _as_list(payload.get("segments")):
        segment = _segment_or_none(item, by_name)
        if segment is not None:
            segments.append(segment)
        if len(segments) == _MAX_SEGMENTS:
            break
    if len(segments) < _MIN_SEGMENTS:
        raise MarketingAnalysisRejected("fewer than 2 grounded audience segments")
    return MarketingAnalysis(
        product=_fact_or_none(payload.get("product"), by_name),
        price=_fact_or_none(payload.get("price"), by_name),
        place=_fact_or_none(payload.get("place"), by_name),
        promotion=_fact_or_none(payload.get("promotion"), by_name),
        segments=tuple(segments),
    )


def analysis_as_dict(analysis: MarketingAnalysis) -> dict:
    def fact(item: QuotedFact | None) -> dict[str, str] | None:
        if item is None:
            return None
        return {"quote": item.quote, "source_name": item.source_name}

    return {
        "product": fact(analysis.product),
        "price": fact(analysis.price),
        "place": fact(analysis.place),
        "promotion": fact(analysis.promotion),
        "segments": [
            {
                "label": segment.label,
                "evidence_quote": segment.evidence_quote,
                "source_name": segment.source_name,
                "channel": segment.channel,
            }
            for segment in analysis.segments
        ],
    }


def hypotheses_from_analysis(
    offer: OfferUnderstanding,
    analysis: MarketingAnalysis,
    geo_radius: GeoRadius,
    *,
    pattern_library: PatternLibrary | None = None,
    business_archetype: str = "",
) -> tuple[Hypothesis, ...]:
    """Search bets from inferred segments, not from copied "serves X" phrases."""

    if not offer.what_we_sell:
        raise ValueError("offer has no service to build a hypothesis around")
    service = offer.what_we_sell[0].text
    geo_suffix = f" near {geo_radius.locality}" if geo_radius.locality else ""
    hypotheses: list[Hypothesis] = []
    for segment in analysis.segments:
        if segment.channel == "business_listing":
            query = f"{segment.label}{geo_suffix or ' in the US'}".strip()
            kind = "business_listing"
            description = f"Companies in the inferred segment: {segment.label}"
        else:
            channel_word = {"forums": "forum", "industry_sites": "", "news": "news"}[segment.channel]
            query = f"{segment.label} {channel_word}{geo_suffix}".strip()
            kind = "demographic_fit"
            description = f"Inferred audience: {segment.label}"
        hypotheses.append(
            Hypothesis(
                audience_segment=segment.label,
                channel=segment.channel,
                query_template=query,
                intent_trigger=IntentTrigger(kind=kind, description=description),
                geo_radius=geo_radius,
                audience_source="ai_inferred",
                evidence_quote=segment.evidence_quote,
            )
        )

    primary = analysis.segments[0]
    for kind, query_template, description in (
        (
            "public_ask",
            f'"looking for {service}"{geo_suffix}'.strip(),
            "Publicly asks for this service right now",
        ),
        (
            "need_statement",
            f'"need {service}"{geo_suffix}'.strip(),
            "Publicly describes needing this service",
        ),
    ):
        hypotheses.append(
            Hypothesis(
                audience_segment=primary.label,
                channel="web_search",
                query_template=query_template,
                intent_trigger=IntentTrigger(kind=kind, description=description),
                geo_radius=geo_radius,
                audience_source="ai_inferred",
                evidence_quote=primary.evidence_quote,
            )
        )

    if pattern_library is not None and business_archetype.strip():
        return _prioritize_by_pattern_library(
            hypotheses, pattern_library.suggest_patterns(business_archetype)
        )
    return tuple(hypotheses)


def _user_prompt(materials: Sequence[DepositedMaterial]) -> str:
    blocks = [f"SOURCE NAME: {item.name}\n{item.body}" for item in materials]
    return "\n\n".join(blocks)


def _parse_json(raw: str) -> dict:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MarketingAnalysisRejected("marketing analysis was not JSON") from exc
    if not isinstance(payload, dict):
        raise MarketingAnalysisRejected("marketing analysis must be a JSON object")
    return payload


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _is_grounded(quote: str, source_name: str, by_name: dict[str, DepositedMaterial]) -> bool:
    try:
        _ground(quote, source_name, by_name)
    except OfferRejected:
        return False
    return True


def _fact_or_none(value: object, by_name: dict[str, DepositedMaterial]) -> QuotedFact | None:
    if not isinstance(value, dict):
        return None
    quote = str(value.get("quote") or "").strip()
    source_name = str(value.get("source_name") or "").strip()
    if not quote or len(quote) > _MAX_QUOTE_CHARS:
        return None
    if not _is_grounded(quote, source_name, by_name):
        return None
    return QuotedFact(quote=quote, source_name=source_name)


def _segment_or_none(value: object, by_name: dict[str, DepositedMaterial]) -> AudienceSegment | None:
    if not isinstance(value, dict):
        return None
    label = str(value.get("label") or "").strip()
    evidence_quote = str(value.get("evidence_quote") or "").strip()
    source_name = str(value.get("source_name") or "").strip()
    channel = str(value.get("channel") or "").strip()
    if not label or len(label) > _MAX_LABEL_CHARS:
        return None
    if channel not in SEARCH_CHANNELS:
        return None
    if not evidence_quote or len(evidence_quote) > _MAX_QUOTE_CHARS:
        return None
    if not _is_grounded(evidence_quote, source_name, by_name):
        return None
    material = by_name[source_name]
    if _label_invents_commercial(label, material.body):
        return None
    return AudienceSegment(
        label=label,
        evidence_quote=evidence_quote,
        source_name=source_name,
        channel=channel,
    )


def _label_invents_commercial(label: str, material_body: str) -> bool:
    body = material_body.casefold()
    return any(match.group(0).casefold() not in body for match in _INVENTED_COMMERCIAL.finditer(label))
