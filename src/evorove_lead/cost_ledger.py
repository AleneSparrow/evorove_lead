"""Token and dollar accounting for cycle 1 classification.

Unknown model prices block a paid call. A missing price is never treated
as zero. Confidence scores are not part of this module.

Prices are microdollars per million tokens, taken from Anthropic's
public pricing page on 12 October 2026
(https://platform.claude.com/docs/en/about-claude/pricing):

- ``claude-sonnet-5-5`` and ``claude-sonnet-5``: $2 / $10 per million input / output tokens
- ``claude-sonnet-4-6``: $3 / $15
- ``claude-haiku-5-5``: $0.10 / $0.50 for prompts at or under 100,000 tokens
- ``claude-haiku-4-5``: $1 / $5

The owner's ``ANTHROPIC_MODEL`` stays Sonnet. Haiku prices are listed so a
known cheaper id is not treated as free. Haiku 5.5 charges more above
100,000 prompt tokens, so this pipeline refuses those prompts instead of
guessing the higher tier. Retired and deprecated ids are absent: a request
that names one is blocked.
"""

from __future__ import annotations

# microdollars per 1,000,000 tokens: (input, output)
MODEL_PRICES_MICROS_PER_MTOK: dict[str, tuple[int, int]] = {
    "claude-sonnet-5-5": (2_000_000, 10_000_000),
    "claude-sonnet-5": (2_000_000, 10_000_000),
    "claude-sonnet-4-6": (3_000_000, 15_000_000),
    "claude-haiku-5-5": (100_000, 500_000),
    "claude-haiku-4-5": (1_000_000, 5_000_000),
}
MAX_PROMPT_TOKENS = 100_000
RECOMMENDED_MODEL = "claude-sonnet-5-5"

DEFAULT_MAX_CALLS_PER_RUN = 40
DEFAULT_MAX_RUN_MICROS = 2_000_000  # $2
DEFAULT_MAX_BUSINESS_MICROS = 25_000_000  # $25
DEFAULT_MAX_GLOBAL_MICROS = 100_000_000  # $100


def cost_micros(model: str, input_tokens: int, output_tokens: int) -> int | None:
    """Return the cost in microdollars, or None when the price is unknown."""

    price = MODEL_PRICES_MICROS_PER_MTOK.get(model)
    if price is None:
        return None
    input_rate, output_rate = price
    return (max(0, input_tokens) * input_rate + max(0, output_tokens) * output_rate) // 1_000_000


class CostLedger:
    """Hard stop for one process-local run, plus prior tenant and global spend."""

    def __init__(
        self,
        model: str,
        *,
        max_calls: int = DEFAULT_MAX_CALLS_PER_RUN,
        max_run_micros: int = DEFAULT_MAX_RUN_MICROS,
        max_business_micros: int = DEFAULT_MAX_BUSINESS_MICROS,
        max_global_micros: int = DEFAULT_MAX_GLOBAL_MICROS,
        prior_business_micros: int = 0,
        prior_global_micros: int = 0,
    ) -> None:
        self.model = model
        self.max_calls = max_calls
        self.max_run_micros = max_run_micros
        self.max_business_micros = max_business_micros
        self.max_global_micros = max_global_micros
        self.prior_business_micros = prior_business_micros
        self.prior_global_micros = prior_global_micros
        self.calls = 0
        self.cache_hits = 0
        self.run_micros = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def reset_run(self) -> None:
        """Start a new search run. Prior tenant and global spend stay."""

        self.calls = 0
        self.cache_hits = 0
        self.run_micros = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def authorize(self, input_tokens: int, output_tokens: int) -> str | None:
        """Return a block reason, or None when a paid call is allowed."""

        if self.model not in MODEL_PRICES_MICROS_PER_MTOK:
            return "unknown_model_price"
        if input_tokens > MAX_PROMPT_TOKENS:
            return "prompt_too_large"
        estimate = cost_micros(self.model, input_tokens, output_tokens)
        if estimate is None:
            return "unknown_model_price"
        if self.calls >= self.max_calls:
            return "max_calls"
        if self.run_micros + estimate > self.max_run_micros:
            return "run_budget"
        if self.prior_business_micros + self.run_micros + estimate > self.max_business_micros:
            return "business_budget"
        if self.prior_global_micros + self.run_micros + estimate > self.max_global_micros:
            return "global_budget"
        return None

    def commit(self, input_tokens: int, output_tokens: int) -> int:
        """Record a call that already happened. Unknown prices return 0 and do not add spend.

        Callers must ``authorize`` before the request. ``commit`` is the
        actual usage from the response, which can differ from the estimate.
        """

        amount = cost_micros(self.model, input_tokens, output_tokens)
        self.calls += 1
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        if amount is None:
            return 0
        self.run_micros += amount
        return amount
