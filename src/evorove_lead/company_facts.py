"""English and US checks that do not call a model."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from evorove_lead.platforms import registrable_domain

_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
    "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming", "District of Columbia",
)
_STATE = re.compile(r"\b(?:" + "|".join(_STATES) + r")\b", re.I)
_PLACE = re.compile(
    r"\b(?:based|headquartered|located|office|address|suite|street|avenue|ave|road|blvd|boulevard)\b",
    re.I,
)
_US_NAMED = re.compile(
    r"\b(?:based|headquartered|located|office|address)\b.{0,65}\b(?:US|USA|United States|U\.S\.)\b",
    re.I,
)
_PLUS1 = re.compile(r"\+1[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_BASIC_LETTER = re.compile(r"[A-Za-z]")
_ANY_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def looks_english(text: str, *, language: str = "") -> bool:
    """True when the page says it is English, or almost all of its letters are basic Latin.

    Vietnamese and other Latin-script languages use letters outside A-Za-z, so a
    Latin-block check alone would keep them. A declared language other than English
    rejects the text even when the letters look familiar.
    """
    declared = (language or "").strip().casefold().replace("_", "-")
    if declared and not declared.startswith("en"):
        return False
    letters = _ANY_LETTER.findall(text or "")
    if len(letters) < 8:
        return False
    basic = len(_BASIC_LETTER.findall(text or ""))
    return basic / len(letters) >= 0.85


def company_root(url: str) -> tuple[str, str] | None:
    """Registrable domain and its https root. A nested path is not a separate company."""
    host = (urlsplit(url).hostname or "").casefold().strip(".")
    domain = registrable_domain(host)
    if not domain or "." not in domain:
        return None
    return domain, f"https://{domain}/"


def confirms_us(text: str) -> bool:
    """A public US fact: a named country, a state next to a place word, or a +1 phone."""
    body = text or ""
    if _US_NAMED.search(body):
        return True
    if _PLUS1.search(body):
        return True
    for state in _STATE.finditer(body):
        window = body[max(0, state.start() - 48):state.end() + 48]
        if _PLACE.search(window):
            return True
    return False
