"""Per-run and optional lifetime caps for LLM and metasearch calls.

An unknown model price is not $0. The call is refused before it is sent.
Token totals are measured when the provider returns usage, otherwise estimated
and flagged. SearxNG is self-hosted: its vendor USD stays unknown, not zero.
"""

from __future__ import annotations

import contextvars
import os
import threading
from dataclasses import dataclass, field

# Published list prices in USD per million tokens. Models absent from this
# table and from LEAD_MODEL_*_USD_PER_MILLION are unpriced on purpose.
_LIST_PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-haiku-20240307": (0.25, 1.25),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
}


class BudgetExhausted(RuntimeError):
    """A hard cap was hit. The run stops; it does not keep calling."""


_LEDGER: contextvars.ContextVar["RunLedger | None"] = contextvars.ContextVar(
    "evorove_lead_run_ledger", default=None
)
_GLOBAL_LOCK = threading.Lock()
_GLOBAL_LLM_CALLS = 0
_GLOBAL_SEARCH_REQUESTS = 0


def reset_global_budget() -> None:
    """Test hook. Production counters live for the process."""

    global _GLOBAL_LLM_CALLS, _GLOBAL_SEARCH_REQUESTS
    with _GLOBAL_LOCK:
        _GLOBAL_LLM_CALLS = 0
        _GLOBAL_SEARCH_REQUESTS = 0


def install_ledger(ledger: "RunLedger | None"):
    return _LEDGER.set(ledger)


def reset_ledger(token) -> None:
    _LEDGER.reset(token)


def current_ledger() -> "RunLedger | None":
    return _LEDGER.get()


def price_for(model: str) -> tuple[float, float] | None:
    """USD per million input and output tokens, or None when the price is unknown."""

    name = (model or "").strip()
    if not name:
        return None
    if name in _LIST_PRICES_USD_PER_MILLION:
        return _LIST_PRICES_USD_PER_MILLION[name]
    raw_in = (os.getenv("LEAD_MODEL_INPUT_USD_PER_MILLION") or "").strip()
    raw_out = (os.getenv("LEAD_MODEL_OUTPUT_USD_PER_MILLION") or "").strip()
    priced_for = (os.getenv("LEAD_PRICED_MODEL") or name).strip()
    if raw_in and raw_out and priced_for == name:
        try:
            return (float(raw_in), float(raw_out))
        except ValueError:
            return None
    return None


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4) if text else 0


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _optional_float_env(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _optional_int_env(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


@dataclass
class StageSpend:
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0
    estimated: bool = False


@dataclass
class RunLedger:
    """Caps for one search run. Thread-safe for the pages fetched inside it."""

    max_llm_calls: int = 24
    max_search_requests: int = 40
    max_usd: float | None = None
    client_id: str = ""
    llm_calls: int = 0
    search_requests: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0
    unknown_price: bool = False
    tokens_estimated: bool = False
    stopped_reason: str = ""
    stages: dict[str, StageSpend] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _prompt_cache: dict[tuple[str, str, str], str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, *, client_id: str = "") -> "RunLedger":
        return cls(
            max_llm_calls=_int_env("LEAD_MAX_LLM_CALLS", 24),
            max_search_requests=_int_env("LEAD_MAX_SEARCH_REQUESTS", 40),
            max_usd=_optional_float_env("LEAD_MAX_USD"),
            client_id=client_id,
        )

    @property
    def search_remaining(self) -> int:
        return max(0, self.max_search_requests - self.search_requests)

    def charge_search(self, stage: str = "discovery") -> None:
        with self._lock:
            self._charge_global_search()
            if self.search_requests >= self.max_search_requests:
                self.stopped_reason = "budget_exhausted"
                raise BudgetExhausted("search request budget exhausted")
            self.search_requests += 1
            bucket = self.stages.setdefault(stage, StageSpend())
            bucket.calls += 1

    def note_search_cache_hit(self, stage: str = "discovery") -> None:
        with self._lock:
            self.cache_hits += 1
            bucket = self.stages.setdefault(stage, StageSpend())
            bucket.cache_hits += 1

    def begin_llm(self, *, stage: str, model: str, system: str, user: str) -> str | None:
        """Return a cached completion, or None if the caller must invoke the model.

        Refuses the call before any network I/O when the price is unknown,
        the call cap is spent, or the dollar cap cannot be proven safe.
        """

        key = (stage, system, user)
        with self._lock:
            cached = self._prompt_cache.get(key)
            if cached is not None:
                self.cache_hits += 1
                bucket = self.stages.setdefault(stage, StageSpend())
                bucket.cache_hits += 1
                return cached
            if price_for(model) is None:
                self.unknown_price = True
                self.cost_usd = None
                bucket = self.stages.setdefault(stage, StageSpend())
                bucket.cost_usd = None
                self.stopped_reason = "unknown_model_price"
                raise BudgetExhausted(f"unknown price for model {model!r}; refusing the call")
            self._charge_global_llm()
            if self.llm_calls >= self.max_llm_calls:
                self.stopped_reason = "budget_exhausted"
                raise BudgetExhausted("LLM call budget exhausted")
            if self.max_usd is not None and self.cost_usd is None:
                self.stopped_reason = "budget_exhausted"
                raise BudgetExhausted("dollar budget cannot be checked because a price was unknown")
            if self.max_usd is not None and (self.cost_usd or 0) >= self.max_usd:
                self.stopped_reason = "budget_exhausted"
                raise BudgetExhausted("dollar budget exhausted")
            self.llm_calls += 1
            return None

    def finish_llm(
        self,
        *,
        stage: str,
        model: str,
        system: str,
        user: str,
        completion: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        estimated = input_tokens is None or output_tokens is None
        in_tokens = input_tokens if input_tokens is not None else estimate_tokens(system) + estimate_tokens(user)
        out_tokens = output_tokens if output_tokens is not None else estimate_tokens(completion)
        price = price_for(model)
        with self._lock:
            self._prompt_cache[(stage, system, user)] = completion
            self.input_tokens += in_tokens
            self.output_tokens += out_tokens
            bucket = self.stages.setdefault(stage, StageSpend())
            bucket.calls += 1
            bucket.input_tokens += in_tokens
            bucket.output_tokens += out_tokens
            if estimated:
                self.tokens_estimated = True
                bucket.estimated = True
            if price is None:
                self.unknown_price = True
                self.cost_usd = None
                bucket.cost_usd = None
                return
            delta = (in_tokens * price[0] + out_tokens * price[1]) / 1_000_000
            if self.cost_usd is None or bucket.cost_usd is None:
                return
            self.cost_usd += delta
            bucket.cost_usd = (bucket.cost_usd or 0) + delta
            if self.max_usd is not None and self.cost_usd > self.max_usd:
                self.stopped_reason = "budget_exhausted"

    def report(self, *, processed: int = 0, qualified: int = 0) -> dict[str, object]:
        cost = self.cost_usd
        per_candidate = None if cost is None or processed <= 0 else cost / processed
        per_qualified = None if cost is None or qualified <= 0 else cost / qualified
        return {
            "client_id": self.client_id,
            "llm_calls": self.llm_calls,
            "llm_cache_hits": self.cache_hits,
            "search_requests": self.search_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tokens_estimated": self.tokens_estimated,
            "cost_usd": cost,
            "unknown_price": self.unknown_price,
            "search_vendor_usd": None,
            "by_stage": {
                name: {
                    "calls": bucket.calls,
                    "cache_hits": bucket.cache_hits,
                    "input_tokens": bucket.input_tokens,
                    "output_tokens": bucket.output_tokens,
                    "cost_usd": bucket.cost_usd,
                    "estimated": bucket.estimated,
                }
                for name, bucket in self.stages.items()
            },
            "candidates_processed": processed,
            "qualified": qualified,
            "cost_per_candidate_usd": per_candidate,
            "cost_per_qualified_usd": per_qualified,
            "stopped_reason": self.stopped_reason,
            "measured_live_provider": False,
        }

    def _charge_global_llm(self) -> None:
        cap = _optional_int_env("LEAD_GLOBAL_MAX_LLM_CALLS")
        if cap is None:
            return
        global _GLOBAL_LLM_CALLS
        with _GLOBAL_LOCK:
            if _GLOBAL_LLM_CALLS >= cap:
                self.stopped_reason = "global_budget_exhausted"
                raise BudgetExhausted("global LLM call budget exhausted")
            _GLOBAL_LLM_CALLS += 1

    def _charge_global_search(self) -> None:
        cap = _optional_int_env("LEAD_GLOBAL_MAX_SEARCH_REQUESTS")
        if cap is None:
            return
        global _GLOBAL_SEARCH_REQUESTS
        with _GLOBAL_LOCK:
            if _GLOBAL_SEARCH_REQUESTS >= cap:
                self.stopped_reason = "global_budget_exhausted"
                raise BudgetExhausted("global search budget exhausted")
            _GLOBAL_SEARCH_REQUESTS += 1


class MeteredCompletion:
    """Wraps a completion port. Identical prompts hit the run cache and do not call out."""

    def __init__(self, inner, ledger: RunLedger, *, stage: str, model: str) -> None:
        self._inner = inner
        self._ledger = ledger
        self._stage = stage
        self._model = model

    def complete(self, *, system: str, user: str) -> str:
        cached = self._ledger.begin_llm(stage=self._stage, model=self._model, system=system, user=user)
        if cached is not None:
            return cached
        text = self._inner.complete(system=system, user=user)
        usage = getattr(self._inner, "last_usage", None) or {}
        in_tokens = usage.get("input_tokens")
        out_tokens = usage.get("output_tokens")
        self._ledger.finish_llm(
            stage=self._stage,
            model=self._model,
            system=system,
            user=user,
            completion=text,
            input_tokens=in_tokens if isinstance(in_tokens, int) and in_tokens > 0 else None,
            output_tokens=out_tokens if isinstance(out_tokens, int) and out_tokens > 0 else None,
        )
        if self._ledger.stopped_reason == "budget_exhausted":
            raise BudgetExhausted("dollar budget exhausted")
        return text


def maybe_meter(completion, stage: str):
    """Wrap only while a run ledger is installed. Tests that never install one stay raw."""

    ledger = current_ledger()
    if ledger is None or completion is None or isinstance(completion, MeteredCompletion):
        return completion
    model = getattr(completion, "_model", "") or ""
    return MeteredCompletion(completion, ledger, stage=stage, model=model)


def client_budget_blocked(spend_llm_calls: int, spend_search_requests: int) -> str:
    """Lifetime caps from the environment. Empty string means the client may run."""

    llm_cap = _optional_int_env("LEAD_CLIENT_MAX_LLM_CALLS")
    search_cap = _optional_int_env("LEAD_CLIENT_MAX_SEARCH_REQUESTS")
    if llm_cap is not None and spend_llm_calls >= llm_cap:
        return "client_budget_exhausted"
    if search_cap is not None and spend_search_requests >= search_cap:
        return "client_budget_exhausted"
    return ""
