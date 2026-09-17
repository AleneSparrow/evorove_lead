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
    # "you"/"your" mark self-referential filler ("for the service you
    # sell", "for your convenience"), not a named audience segment. A real
    # audience phrase names who the reader's customer is, never the
    # reader themselves -- found piloting this against evorove.com's own
    # FAQ copy, which is full of "you"/"your" describing the reader, not
    # an audience to search for.
    "you", "your",
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


def _is_shouting(text: str) -> bool:
    """An ALL-CAPS line is a hero banner/CTA style, not a literal sentence.

    Real bug found piloting evorove.com: its own H1 is "COLD IN. DONE ON
    THE BOARD." -- a shouted tagline, not a description of the service --
    and taking it verbatim as `what_we_sell` seeded search queries and a
    fit-check vocabulary from ordinary English words ("cold", "board",
    "done") that collide with completely unrelated pages, not the offer.
    """

    letters = [character for character in text if character.isalpha()]
    if len(letters) < 4:
        return False
    upper = sum(1 for character in letters if character.isupper())
    return upper / len(letters) > 0.8


_MIN_SERVICE_LINE_WORDS = 4
# A real heading or tagline is short. `page_material` puts each heading and
# the page <title> on its own line *before* the full page body -- but that
# final body line is the page's whole running text, long enough that it
# would never plausibly be a single heading. Bounding candidates to
# heading-length keeps this fallback inside the heading/title lines it is
# meant for, instead of spilling into that body blob.
_MAX_SERVICE_LINE_CHARS = 120


def _first_line(body: str) -> str:
    """The first line worth reading as a plain sentence, skipping shouted or bare ones.

    Prefers the first non-shouting heading/title line with enough words
    to be a real sentence -- not a full-caps hero tagline, and not just
    the page <title> (often only the business's own name, one or two
    words, once the tagline is skipped). Falls back to the literal first
    line if nothing qualifies, so a real all-caps or terse site still
    gets an offer instead of none at all.
    """

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for text in lines:
        if (
            len(text) <= _MAX_SERVICE_LINE_CHARS
            and not _is_shouting(text)
            and len(_WORD_RE.findall(text)) >= _MIN_SERVICE_LINE_WORDS
        ):
            return text
    return lines[0] if lines else ""


def _service_phrase(heading: str) -> str:
    parts = re.split(r"\s+for\s+", heading, maxsplit=1, flags=re.IGNORECASE)
    service = parts[0].strip()
    return service if len(service) >= 3 else heading


# A sentence phrased as a question ("What does Done mean for an offline
# business?") is an FAQ heading about the product, not a declaration of
# who the offer is for -- matching "for X" inside one produces an
# audience phrase that is really a fragment of the question. Found
# piloting against evorove.com's own FAQ-heavy copy, where several FAQ
# headings otherwise outscored the page's one real audience mention.
_QUESTION_RE = re.compile(r"[^.!?\n]*\?")

# Words that only restate the offer itself ("for the service you sell",
# "for your product") rather than naming who it is for. A phrase built
# entirely from these words is filler, not an audience.
_GENERIC_AUDIENCE_FILLER_WORDS = frozenset({"the", "a", "an", "your", "you", "service", "product", "sell", "offer"})


def _is_generic_filler(phrase: str) -> bool:
    words = {word.casefold() for word in _WORD_RE.findall(phrase)}
    if bool(words) and words <= _GENERIC_AUDIENCE_FILLER_WORDS:
        return True
    # "Cold is people found in the open field -- not visitors who already
    # filled your form" (the contract's own words): a phrase describing
    # the reader's own site visitors is the one audience this product is
    # explicitly not allowed to search for, so it is never a real
    # audience claim even when grammatically "for X".
    return "site" in words or "visitor" in words or "visitors" in words


def _audience_phrases(body: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[\n.]+", _QUESTION_RE.sub(" ", body)):
        for match in FOR_PATTERN.finditer(chunk):
            phrase = match.group(1).strip(" -,")
            key = phrase.casefold()
            if len(phrase) < 3 or key in seen:
                continue
            if _is_negated_before(chunk, match.start()):
                continue
            if _is_generic_filler(phrase):
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
