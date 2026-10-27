"""Historical manual extraction CLI, not part of the live cycle-1 path.

One Haiku read of saved company text. This module does not call a network on import.

The live command refuses to run unless ANTHROPIC_API_KEY is already set.
An unknown model price blocks the call. A missing price is never treated as zero.
The quote in a reply is kept only when it is a substring of the saved text and
at most 280 characters. Cache key is the SHA-256 of that text. One call per company.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field

from evorove_lead.cost_ledger import MAX_PROMPT_TOKENS, CostLedger, cost_micros

MODEL = "claude-haiku-5-5"
EFFORT = "low"
MAX_OUTPUT_TOKENS = 300
DEFAULT_MAX_RUN_USD = 0.50
MAX_QUOTE_CHARS = 280
ENV_CAP = "QUALIFICATION_EXTRACT_MAX_USD"

_SYSTEM = (
    "Read only the saved company page text in the user message. "
    "Return one JSON object and nothing else: "
    '{"name": "", "role": "", "quote": "", "team_size": "", "country": ""}. '
    "quote must be copied verbatim from the text, at most 280 characters, "
    "and must say what the company sells and to whom. "
    "Leave a field empty when the text does not state it. "
    "Do not invent a price, a discount, a guarantee, a legal claim, or an email address."
)


@dataclass
class ScriptedExtraction:
    """Fixed reply for tests. Does not open a socket."""

    reply_text: str
    input_tokens: int = 20
    output_tokens: int = 40
    calls: int = 0

    def complete(self, *, system: str, user: str, max_tokens: int, effort: str) -> "ScriptedExtraction":
        del system, user
        if max_tokens != MAX_OUTPUT_TOKENS or effort != EFFORT:
            raise AssertionError("extraction must ask for 300 tokens at low effort")
        self.calls += 1
        return self


@dataclass
class ExtractionResult:
    called: bool = False
    reason: str = ""
    name: str = ""
    role: str = ""
    quote: str = ""
    team_size: str = ""
    country: str = ""
    cache_hit: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class SavedTextExtractor:
    completion: object | None = None
    model: str = MODEL
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    effort: str = EFFORT
    max_run_usd: float | None = None
    cache: dict = field(default_factory=dict)
    _companies: set = field(default_factory=set)
    ledger: CostLedger | None = None

    def __post_init__(self) -> None:
        cap = self.max_run_usd
        if cap is None:
            cap = _cap_from_env()
        if cap < 0:
            raise ValueError("extraction spend cap cannot be negative")
        self.max_run_usd = cap
        if self.ledger is None:
            self.ledger = CostLedger(
                self.model,
                max_calls=10_000,
                max_run_micros=int(round(cap * 1_000_000)),
            )

    def extract(self, company_id: str, text: str) -> dict:
        digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
        cached = self.cache.get(digest)
        if cached is not None:
            self.ledger.cache_hits += 1
            return {**cached, "called": False, "cache_hit": True, "reason": "cache"}
        if company_id in self._companies:
            return _result(reason="already_called")
        if self.completion is None:
            return _result(reason="no_model")
        if cost_micros(self.model, 1, 1) is None:
            return _result(reason="unknown_model_price")
        input_tokens = _estimate_tokens(_SYSTEM, text or "")
        block = self.ledger.authorize(input_tokens, self.max_output_tokens)
        if block:
            return _result(reason=block)
        reply = self.completion.complete(
            system=_SYSTEM, user=text or "", max_tokens=self.max_output_tokens, effort=self.effort,
        )
        actual_in = int(getattr(reply, "input_tokens", input_tokens))
        actual_out = int(getattr(reply, "output_tokens", 0))
        if cost_micros(self.model, actual_in, actual_out) is None:
            return _result(reason="unknown_model_price")
        self.ledger.commit(actual_in, actual_out)
        self._companies.add(company_id)
        parsed = _accept(getattr(reply, "reply_text", None) or getattr(reply, "text", ""), text or "")
        parsed["input_tokens"] = actual_in
        parsed["output_tokens"] = actual_out
        parsed["called"] = True
        self.cache[digest] = dict(parsed)
        return parsed


def _result(**kwargs) -> dict:
    base = ExtractionResult(**kwargs)
    return {
        "called": base.called, "reason": base.reason, "name": base.name, "role": base.role,
        "quote": base.quote, "team_size": base.team_size, "country": base.country,
        "cache_hit": base.cache_hit, "input_tokens": base.input_tokens, "output_tokens": base.output_tokens,
    }


def _cap_from_env() -> float:
    raw = os.environ.get(ENV_CAP)
    if raw is None or not str(raw).strip():
        return DEFAULT_MAX_RUN_USD
    return float(raw)


def _estimate_tokens(system: str, user: str) -> int:
    # One token per four UTF-8 bytes, plus a small frame. Over 100k blocks the call.
    estimate = (len(system.encode("utf-8")) + len(user.encode("utf-8")) + 64) // 4
    return min(estimate, MAX_PROMPT_TOKENS + 1)


def _accept(raw: str, text: str) -> dict:
    payload = _parse_json(raw)
    if payload is None:
        return _result(called=True, reason="invalid_json")
    quote = str(payload.get("quote") or "").strip()
    if len(quote) > MAX_QUOTE_CHARS or quote not in text:
        quote = ""
    team = str(payload.get("team_size") or "").strip()
    if team and not any(part and part in text for part in re.findall(r"\d+", team)):
        team = ""
    country = str(payload.get("country") or "").strip()
    if country and country.casefold() not in text.casefold():
        country = ""
    name = str(payload.get("name") or "").strip()
    role = str(payload.get("role") or "").strip()
    if name and name.casefold() not in text.casefold():
        name, role = "", ""
    if role and role.casefold() not in text.casefold():
        role = ""
    return {
        "called": True, "reason": "", "name": name, "role": role, "quote": quote,
        "team_size": team, "country": country, "cache_hit": False, "input_tokens": 0, "output_tokens": 0,
    }


def _parse_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def main(argv: list[str] | None = None) -> int:
    """Paid entry point. Refuses to run unless the owner already exported a key."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        print(
            "ANTHROPIC_API_KEY is unset. Refusing the paid Haiku extraction. No request was sent.",
            file=sys.stderr,
        )
        return 2
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    if args.limit < 1:
        print("limit must be at least 1", file=sys.stderr)
        return 2
    from sqlalchemy import create_engine, text
    engine = create_engine(args.database_url, connect_args={"options": "-c default_transaction_read_only=on"})
    with engine.connect() as conn:
        rows = conn.execute(text(
            "select domain, string_agg(text, chr(10)) as body from raw_pages group by domain order by domain"
        )).mappings().all()
    extractor = SavedTextExtractor(AnthropicFieldCompletion(os.environ["ANTHROPIC_API_KEY"]))
    used = 0
    for row in rows:
        if used >= args.limit:
            break
        body = row["body"] or ""
        if not body.strip():
            continue
        result = extractor.extract(row["domain"], body)
        used += 1
        print(json.dumps({
            "domain": row["domain"],
            "called": result.get("called"),
            "reason": result.get("reason"),
            "name": result.get("name"),
            "role": result.get("role"),
            "quote": result.get("quote"),
            "team_size": result.get("team_size"),
            "country": result.get("country"),
            "cache_hit": result.get("cache_hit"),
        }, ensure_ascii=False))
        if result.get("reason") in {"run_budget", "unknown_model_price", "prompt_too_large", "max_calls"}:
            break
    print(f"companies={used} calls={extractor.ledger.calls} cache_hits={extractor.ledger.cache_hits} run_micros={extractor.ledger.run_micros}", file=sys.stderr)
    return 0


class AnthropicFieldCompletion:
    """One HTTPS call. The key stays on the request header and out of repr."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def __repr__(self) -> str:
        return "AnthropicFieldCompletion(model='claude-haiku-5-5')"

    def complete(self, *, system: str, user: str, max_tokens: int, effort: str):
        import urllib.request
        if max_tokens != MAX_OUTPUT_TOKENS or effort != EFFORT:
            raise RuntimeError("field extraction must use 300 tokens and low effort")
        if cost_micros(MODEL, 1, 1) is None:
            raise RuntimeError("unknown_model_price")
        payload = {
            "model": MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user[:120_000]}],
            "output_config": {"effort": EFFORT},
        }
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": self._api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read(2_000_000))
        parts = [
            block.get("text", "")
            for block in body.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        usage = body.get("usage") or {}
        reply = type("Reply", (), {})()
        reply.reply_text = "".join(parts)
        reply.text = reply.reply_text
        reply.input_tokens = int(usage.get("input_tokens") or 0)
        reply.output_tokens = int(usage.get("output_tokens") or 0)
        return reply


if __name__ == "__main__":
    raise SystemExit(main())
