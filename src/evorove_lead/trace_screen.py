"""Deterministic trace gates: language, geography, recency. No LLM."""

from __future__ import annotations

import re
from datetime import datetime, timezone

# Country and province names. US city names are omitted so "Paris, Texas"
# is not dropped. Mexico is outside the US market this product searches.
_FOREIGN = re.compile(
    r"\b(canada|ontario|british columbia|united kingdom|australia|india|"
    r"germany|france|mexico|brazil|spain|italy|china|japan)\b",
    re.IGNORECASE,
)
_LATIN = re.compile(r"[A-Za-z]")
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)

DEFAULT_MAX_AGE_DAYS = 548


def screen_snippet(
    snippet: str,
    *,
    locality: str = "",
    published_at: datetime | None = None,
    now: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_AGE_DAYS,
) -> tuple[str, str, str]:
    """Return (language, geo_hint, reject_reason). Empty reject_reason means keep.

    Missing dates are kept. A date is never invented.
    """

    text = snippet or ""
    language = _language(text)
    geo = (locality or "").strip()
    if language == "non_latin":
        return language, geo, "language is outside the US English brief"
    if _FOREIGN.search(text) and not _mentions(text, geo):
        return language, "outside-us", "outside the brief's geography"
    if published_at is not None and max_age_days is not None:
        moment = now or datetime.now(timezone.utc)
        published = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        age_days = (moment - published).days
        if age_days > max_age_days:
            return language, geo, "trace is older than the recency window"
    return language, geo, ""


def _language(text: str) -> str:
    letters = _LETTERS.findall(text)
    if not letters:
        return "und"
    latin = sum(1 for char in letters if _LATIN.match(char))
    if latin / len(letters) < 0.7:
        return "non_latin"
    return "en"


def _mentions(text: str, locality: str) -> bool:
    if not locality:
        return False
    return locality.casefold() in text.casefold()
