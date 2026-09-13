"""Propose an offer only from quotes already on the business page."""

from __future__ import annotations

import re
from typing import Sequence

from evorove_lead.materials import DepositedMaterial
from evorove_lead.offer import (
    CommercialClaim,
    GroundedClaim,
    OfferUnderstanding,
    accept_offer_understanding,
)

# Up to six words after the trigger word. Six, not three: "small local
# service businesses" alone is four words -- a real audience phrase found
# piloting this against evorove.com's own copy that the old three-word cap
# silently dropped. Each word in the phrase is checked against
# _AUDIENCE_STOP_WORDS *as it's consumed* (a negative lookahead per word),
# not only at the end: a plain end-of-phrase lookahead lets a greedy match
# run straight past a stop word whenever a period follows it anyway
# ("busy parents in town." would otherwise swallow "in town" too, since
# reaching the "." still satisfies an end-of-match check).
#
# Trigger words beyond "for": a business can name its audience as "serves
# X" or "helps X" without ever writing the word "for" at all.
_AUDIENCE_TRIGGER = r"(?:for|serves|serving|help|helps|helping|works with)"
_AUDIENCE_STOP_WORDS = (
    "in", "that", "who", "with", "we", "our", "and", "or", "but", "so",
    "since", "before", "after", "because", "while", "when", "if", "as",
)
_NOT_A_STOP_WORD = rf"(?!\b(?:{'|'.join(_AUDIENCE_STOP_WORDS)})\b)"
_AUDIENCE_WORD = rf"{_NOT_A_STOP_WORD}[A-Za-z][A-Za-z']*"
FOR_PATTERN = re.compile(
    rf"\b{_AUDIENCE_TRIGGER}\s+({_AUDIENCE_WORD}(?:\s+{_AUDIENCE_WORD}){{0,5}})",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(r"\$\d+(?:,\d{3})*(?:\.\d{2})?")

# A negated "for X" ("Nobody builds a version for your company") is not an
# audience claim -- the opposite of one. Checked against the few words
# immediately before "for" in the same chunk.
_NEGATION_WORDS = frozenset(
    {
        "no", "nobody", "none", "not", "never", "without", "nor", "cannot",
        "isn't", "aren't", "wasn't", "weren't", "doesn't", "don't", "didn't",
        "won't", "wouldn't", "can't", "couldn't", "shouldn't", "hasn't",
        "haven't", "hadn't",
    }
)
_WORD_RE = re.compile(r"[A-Za-z']+")
_NEGATION_LOOKBACK_WORDS = 5


def _is_negated_before(chunk: str, match_start: int) -> bool:
    preceding_words = _WORD_RE.findall(chunk[:match_start])[-_NEGATION_LOOKBACK_WORDS:]
    return any(word.casefold() in _NEGATION_WORDS for word in preceding_words)


def _first_line(body: str) -> str:
    for line in body.splitlines():
        text = line.strip()
        if text:
            return text
    return ""


def _service_phrase(heading: str) -> str:
    parts = re.split(r"\s+for\s+", heading, maxsplit=1, flags=re.IGNORECASE)
    service = parts[0].strip()
    return service if len(service) >= 3 else heading


def _audience_phrases(body: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[\n.]+", body):
        for match in FOR_PATTERN.finditer(chunk):
            phrase = match.group(1).strip(" -,")
            key = phrase.casefold()
            if len(phrase) < 3 or key in seen:
                continue
            if _is_negated_before(chunk, match.start()):
                continue
            seen.add(key)
            found.append(phrase)
            if len(found) == 3:
                return tuple(found)
    return tuple(found)


def _price_claims(body: str, source_name: str) -> tuple[CommercialClaim, ...]:
    seen: set[str] = set()
    claims: list[CommercialClaim] = []
    for match in PRICE_PATTERN.finditer(body):
        amount = match.group(0)
        if amount in seen:
            continue
        seen.add(amount)
        claims.append(CommercialClaim(kind="price", text=amount, source_name=source_name))
    return tuple(claims)


def read_offer(materials: Sequence[DepositedMaterial]) -> OfferUnderstanding:
    """Turn the business's own words into a grounded offer, or raise."""

    what_we_sell: list[GroundedClaim] = []
    who_may_fit: list[GroundedClaim] = []
    commercial: list[CommercialClaim] = []
    sold_keys: set[str] = set()
    audience_keys: set[str] = set()

    for material in materials:
        heading = _service_phrase(_first_line(material.body))
        if heading:
            key = heading.casefold()
            if key not in sold_keys:
                sold_keys.add(key)
                what_we_sell.append(GroundedClaim(text=heading, source_name=material.name))
        for phrase in _audience_phrases(material.body):
            key = phrase.casefold()
            if key in audience_keys:
                continue
            audience_keys.add(key)
            who_may_fit.append(GroundedClaim(text=phrase, source_name=material.name))
        commercial.extend(_price_claims(material.body, material.name))

    return accept_offer_understanding(
        materials=materials,
        what_we_sell=what_we_sell,
        who_may_fit=who_may_fit,
        commercial_claims=commercial,
    )
