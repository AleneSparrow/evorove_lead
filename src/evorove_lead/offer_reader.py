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

# First word may be capitalized; later words must stay lowercase so a new
# sentence ("We cook…") does not get swallowed into the audience phrase.
FOR_PATTERN = re.compile(
    r"\bfor\s+((?:[A-Za-z][A-Za-z']*)(?:\s+[a-z][A-Za-z']*){0,3})",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(r"\$\d+(?:,\d{3})*(?:\.\d{2})?")


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
    for match in FOR_PATTERN.finditer(body):
        phrase = re.split(r"\s+in\s+", match.group(1).strip(), maxsplit=1)[0]
        phrase = phrase.strip(" -,")
        key = phrase.casefold()
        if len(phrase) < 3 or key in seen:
            continue
        seen.add(key)
        found.append(phrase)
        if len(found) == 3:
            break
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
