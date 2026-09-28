"""Module 3's LLM fallback: an About/Team page the regex heuristic can't parse.

`decision_maker.py`'s heuristic only catches a name sitting in plain text
next to a role word. A page that names its owner in a photo caption, a
bulleted bio list, or a sentence structure the regex doesn't anticipate
finds nothing there. This reads the same already-fetched pages through an
LLM instead, with the same grounding discipline as `marketing_analysis.py`:
the model's email and evidence_quote must be verbatim substrings of the
page it read them from, on the company's own domain, and not a generic
business inbox. An ungrounded or generic answer is dropped, not passed
through.

The default provider does not call a network. `AI_PROVIDER=anthropic` is
the live reader, gated by the same `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`
the owner already sets for `marketing_analysis.py` -- one setting turns
both LLM reads on, not two.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Protocol, Sequence

from evorove_lead.decision_maker import DecisionMaker, _GENERIC_LOCAL_PARTS
from evorove_lead.platforms import registrable_domain

_MAX_QUOTE_CHARS = 800
_MAX_INPUT_CHARS = 6000

_SYSTEM = """You read one page from a small US business's own website.
Find the person who owns or runs this business -- founder, owner, CEO,
president, or director -- and the email address on THIS page that is
theirs, not a general company inbox such as info@ or support@.

Return one JSON object and nothing else:
{"name": "<their name>", "role": "<their role, as stated or implied>", "email": "<their email, copied verbatim from the page>", "evidence_quote": "<verbatim substring of the page that names them and their role>"}

If the page does not name a specific owner/founder/director, or does not
show an email address that is clearly theirs and not a shared inbox,
return {"name": null, "role": null, "email": null, "evidence_quote": null}.
"""


class DecisionMakerLLMUnavailable(Exception):
    """The LLM read failed or is not configured. Treated the same as "no answer"."""


class DecisionMakerCompletion(Protocol):
    def complete(self, *, system: str, user: str) -> str:
        """Return the model's raw text. Must not include an API key."""


class ScriptedDecisionMakerCompletion:
    """A fixed reply for tests. Never opens a socket."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        del system, user
        return self._response


class DeterministicDecisionMakerCompletion:
    """Local fallback. Does not read a page and does not call a network.

    Wiring this into the engine fails the read closed. Tests that need a
    finished reading use `ScriptedDecisionMakerCompletion`.
    """

    def complete(self, *, system: str, user: str) -> str:
        del system, user
        raise DecisionMakerLLMUnavailable("deterministic provider does not read pages")


class AnthropicDecisionMakerCompletion:
    """HTTPS call to Anthropic. The key stays on the request header."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def __repr__(self) -> str:
        return f"AnthropicDecisionMakerCompletion(model={self._model!r})"

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
            raise DecisionMakerLLMUnavailable(f"anthropic request failed: HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise DecisionMakerLLMUnavailable("anthropic request failed") from None
        parts = [
            block.get("text", "")
            for block in body.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)


def decision_maker_completion_from_env() -> DecisionMakerCompletion | None:
    """Live reader only when the owner set `AI_PROVIDER=anthropic`.

    Mirrors `marketing_analysis.marketing_analyzer_from_env()` exactly, so
    the one `AI_PROVIDER` setting gates both LLM reads together, not two
    settings the owner has to keep in sync.
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
    return AnthropicDecisionMakerCompletion(api_key=api_key, model=model)


def find_decision_maker_llm(
    pages: Sequence[tuple[str, str]], domain: str, llm: DecisionMakerCompletion
) -> DecisionMaker | None:
    """Try the LLM on each already-fetched page in order; first grounded answer wins."""

    for url, text in pages:
        if not text.strip():
            continue
        try:
            raw = llm.complete(system=_SYSTEM, user=text[:_MAX_INPUT_CHARS])
        except DecisionMakerLLMUnavailable:
            continue
        result = _accept(raw, url, domain, text)
        if result is not None:
            return result
    return None


def _accept(raw: str, url: str, domain: str, page_text: str) -> DecisionMaker | None:
    payload = _parse_json(raw)
    if payload is None:
        return None
    name = str(payload.get("name") or "").strip()
    role = str(payload.get("role") or "").strip()
    email = str(payload.get("email") or "").strip().casefold()
    evidence_quote = str(payload.get("evidence_quote") or "").strip()
    if not name or not role or not email or not evidence_quote:
        return None
    if len(evidence_quote) > _MAX_QUOTE_CHARS or evidence_quote not in page_text:
        return None
    local, _, email_domain = email.partition("@")
    if not local or not email_domain:
        return None
    if registrable_domain(email_domain) != domain:
        return None
    if local in _GENERIC_LOCAL_PARTS:
        return None
    if email not in page_text.casefold():
        return None
    return DecisionMaker(name=name, role=role, email=email, evidence_quote=evidence_quote, source_url=url)


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
