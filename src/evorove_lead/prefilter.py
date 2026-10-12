"""Drop obvious noise before any model call.

Missing service keywords are not a reason to drop a page. Writing system
and language are not a reason either. Spam, an invalid source, a known
non-US country code, and a publication date older than five years are.
"""

from __future__ import annotations

from datetime import date, datetime

from evorove_lead.presence import PresenceRejected, validate_public_http_url

SPAM_MARKERS = (
    "viagra",
    "cialis",
    "buy followers",
    "casino bonus",
    "crypto airdrop",
    "work from home $$$",
    "seo package",
)
MAX_AGE_DAYS = 365 * 5


def looks_like_spam(text: str) -> bool:
    folded = (text or "").casefold()
    return any(marker in folded for marker in SPAM_MARKERS)


def _parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    candidate = raw[:10]
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def prefilter_reason(
    text: str,
    url: str,
    *,
    publication_date: str = "",
    geo_hint: str = "",
    now: datetime | None = None,
) -> str | None:
    """Return a reject reason, or None when the candidate stays eligible."""

    if not (text or "").strip():
        return "empty page"
    if looks_like_spam(text):
        return "obvious spam"
    try:
        validate_public_http_url(url)
    except PresenceRejected as exc:
        return str(exc)
    published = _parse_date(publication_date)
    if published is not None:
        today = (now or datetime.now()).date()
        if (today - published).days > MAX_AGE_DAYS:
            return "outdated publication"
    hint = (geo_hint or "").strip().upper()
    if len(hint) == 2 and hint.isalpha() and hint not in {"US"}:
        return "outside the US market"
    return None
