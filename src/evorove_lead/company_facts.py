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
    r"\b(?:based|headquartered|located|office|address|suite|street|avenue|ave|road|blvd|boulevard|contact|phone)\b",
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


_CITIES = (
    "Atlanta", "Austin", "Baltimore", "Boston", "Charlotte", "Chicago", "Cincinnati",
    "Cleveland", "Columbus", "Dallas", "Denver", "Detroit", "Houston", "Indianapolis",
    "Jacksonville", "Las Vegas", "Los Angeles", "Memphis", "Miami", "Milwaukee",
    "Minneapolis", "Nashville", "New Orleans", "New York", "Orlando", "Philadelphia",
    "Phoenix", "Pittsburgh", "Portland", "Sacramento", "San Antonio", "San Diego",
    "San Francisco", "San Jose", "Seattle", "St. Louis", "Tampa", "Tucson", "Washington",
)
_CITY = re.compile(r"\b(?:" + "|".join(_CITIES) + r")\b", re.I)
_DOLLAR = re.compile(r"\$\s*\d")
_US_PHRASE = re.compile(r"\b(?:United States|U\.S\.|USA)\b", re.I)
_SERVING = re.compile(
    r"\bserving clients across (?:the )?(?:US|U\.S\.|United States|North America)\b",
    re.I,
)
_SCHEMA_US = re.compile(r"^(?:us|usa|u\.s\.|united states)$", re.I)
_TEXT_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+20\d{2}\b|\b20\d{2}-\d{2}-\d{2}\b",
    re.I,
)


def parse_page_date(value: str):
    """A date written on the page. Empty input is not the collection clock."""
    from datetime import datetime, timezone
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                parsed = None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def date_in_sentence(sentence: str) -> str:
    match = _TEXT_DATE.search(sentence or "")
    return match.group(0) if match else ""


def assess_us(text: str, *, domain: str = "", schema_country: str = "") -> dict:
    """How firmly the page places the company in the US.

    One strong sign is enough: a schema.org country, a .us domain, a +1 phone,
    or a state or city next to an office or contact. Those are hard to meet by
    accident. A dollar price or the words United States can appear on a site
    outside the US, so one of those is needs_review and two different weak
    signs are required before they confirm. A .com domain and English text are
    not signs. An explicit non-US schema country is absent.
    """
    body = text or ""
    declared = (schema_country or "").strip()
    if not declared:
        marker = re.search(r"\baddressCountry\s+([A-Za-z][A-Za-z .]{1,20})", body)
        if marker:
            declared = marker.group(1).strip(" .")
    if declared and not _SCHEMA_US.match(declared):
        return {"status": "absent", "strong": (), "weak": ()}
    strong = []
    weak = []
    if declared and _SCHEMA_US.match(declared):
        strong.append("schema_country")
    host = (domain or "").casefold().strip(".")
    if host.endswith(".us"):
        strong.append("us_domain")
    if _PLUS1.search(body):
        strong.append("phone")
    if _US_NAMED.search(body) or _near_place(body, _STATE) or _near_place(body, _CITY):
        strong.append("place")
    if _DOLLAR.search(body):
        weak.append("dollars")
    serving = _SERVING.search(body)
    if serving:
        weak.append("serving")
    phrase = _US_PHRASE.search(body)
    if phrase and not (serving and serving.start() <= phrase.start() < serving.end()):
        if not _US_NAMED.search(body):
            weak.append("country_phrase")
    if strong or len(weak) >= 2:
        status = "confirmed"
    elif weak:
        status = "needs_review"
    else:
        status = "absent"
    return {"status": status, "strong": tuple(strong), "weak": tuple(weak)}


def _near_place(body: str, pattern: re.Pattern[str]) -> bool:
    for match in pattern.finditer(body):
        window = body[max(0, match.start() - 48):match.end() + 48]
        if _PLACE.search(window):
            return True
    return False


def confirms_us(text: str) -> bool:
    """True only when assess_us would send the company on, not when it needs review."""
    return assess_us(text)["status"] == "confirmed"
