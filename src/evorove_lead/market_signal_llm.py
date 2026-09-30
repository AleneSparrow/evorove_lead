"""Track 4: does one piece of market news justify searching for a customer right now?

The plan calls for "своё хранилище новостей/сигналов ... LLM-фильтрация
релевантности, связь с построением гипотез в модуле 1". This is that
filter. `market_signals.py` finds raw industry-news search hits; this reads
one hit against the business's own archetype and decides two things: is it
actually relevant, and if so, who does it suggest searching for and where.

Same grounding discipline as marketing_analysis.py and decision_maker_llm.py:
the evidence_quote must be a verbatim substring of the news item's own text
(headline + snippet), never invented. A signal that fails grounding, or
that the model judged irrelevant, is dropped -- it never becomes a search.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

_MAX_QUOTE_CHARS = 800
_MAX_LABEL_CHARS = 255
SIGNAL_CHANNELS = ("forums", "industry_sites", "news", "business_listing")

_SYSTEM = """You read one piece of US market/industry news against one business's
own archetype (what kind of business it is). Decide whether this news is a
live, concrete reason to search for a customer for that business right now
-- not just a topic in the same general industry.

Return one JSON object and nothing else:
{"relevant": true|false, "segment_label": "<who to search for, or null>", "channel": "forums"|"industry_sites"|"news"|"business_listing"|null, "evidence_quote": "<verbatim substring of the news text that justifies this, or null>"}

Set relevant to false, and every other field to null, unless the news names
or clearly implies a concrete audience worth searching for today. Do not
invent urgency the text doesn't state. evidence_quote must be copied
verbatim from the news text given to you.
"""


class MarketSignalLLMUnavailable(Exception):
    """The LLM read failed or is not configured. Treated the same as "not relevant"."""


class MarketSignalCompletion(Protocol):
    def complete(self, *, system: str, user: str) -> str:
        """Return the model's raw text. Must not include an API key."""


class ScriptedMarketSignalCompletion:
    """A fixed reply for tests. Never opens a socket."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        del system, user
        return self._response


class DeterministicMarketSignalCompletion:
    """Local fallback. Does not read a signal and does not call a network."""

    def complete(self, *, system: str, user: str) -> str:
        del system, user
        raise MarketSignalLLMUnavailable("deterministic provider does not read market signals")


class AnthropicMarketSignalCompletion:
    """HTTPS call to Anthropic. The key stays on the request header."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def __repr__(self) -> str:
        return f"AnthropicMarketSignalCompletion(model={self._model!r})"

    def complete(self, *, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "max_tokens": 500,
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
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MarketSignalLLMUnavailable(f"anthropic request failed: HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise MarketSignalLLMUnavailable("anthropic request failed") from None
        parts = [
            block.get("text", "")
            for block in body.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)


def market_signal_completion_from_env() -> MarketSignalCompletion | None:
    """Mirrors marketing_analyzer_from_env(): the same AI_PROVIDER gate.

    One owner setting turns on all three LLM reads (module 1, module 3's
    fallback, and this one) together.
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
    return AnthropicMarketSignalCompletion(api_key=api_key, model=model)


@dataclass(frozen=True)
class RelevantSignal:
    """A grounded, LLM-accepted reason to search for a customer right now."""

    segment_label: str
    channel: str
    evidence_quote: str


def evaluate_signal(
    snippet: str, business_archetype: str, llm: MarketSignalCompletion
) -> RelevantSignal | None:
    """None on any failure to read, parse, or ground -- never a guess."""

    text = (snippet or "").strip()
    if not text:
        return None
    user = f"Business archetype: {business_archetype or 'unspecified'}\n\nNews:\n{text}"
    try:
        raw = llm.complete(system=_SYSTEM, user=user)
    except MarketSignalLLMUnavailable:
        return None
    return _accept(raw, text)


def _accept(raw: str, source_text: str) -> RelevantSignal | None:
    payload = _parse_json(raw)
    if payload is None or payload.get("relevant") is not True:
        return None
    label = str(payload.get("segment_label") or "").strip()
    channel = str(payload.get("channel") or "").strip()
    evidence_quote = str(payload.get("evidence_quote") or "").strip()
    if not label or len(label) > _MAX_LABEL_CHARS:
        return None
    if channel not in SIGNAL_CHANNELS:
        return None
    if not evidence_quote or len(evidence_quote) > _MAX_QUOTE_CHARS:
        return None
    if evidence_quote not in source_text:
        return None
    return RelevantSignal(segment_label=label, channel=channel, evidence_quote=evidence_quote)


def _parse_json(raw: str) -> dict | None:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
