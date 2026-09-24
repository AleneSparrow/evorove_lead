"""Re-selection of a found trace: fit, evidence, addressable (roadmap step 18).

Replaces "the service phrase appears verbatim in the reason". Every word a
trace is matched on comes from the client's own materials (its site / brief):
no industry lists, no invented synonyms. A trace is kept only when

- **fit**: it shares a distinctive service word (a person's need) or an
  audience word (a business of the kind the client serves) with the brief;
- **evidence**: it states an open need ("need", "looking for", "broke"...)
  or names the audience the brief serves;
- **addressable**: there is someone to write to;

and it is not a competitor: a business advertising the client's own service
is rejected, as is a neighbouring niche that shares none of these words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from evorove_lead.materials import DepositedMaterial
from evorove_lead.offer import OfferUnderstanding

SOURCE_KIND_PERSON = "person"  # someone's own public post / an owner observation
SOURCE_KIND_BUSINESS = "business"  # a company's own website (B2B)

NOT_TIED = "reason is not tied to the offer"
COMPETITOR = "competitor advertising the same service"
NO_EVIDENCE = "no open need or audience fact in the reason"
NOT_ADDRESSABLE = "nobody to address"

_WORD_RE = re.compile(r"[a-z][a-z']{2,}")
_STOPWORDS = frozenset(
    """
    the and for with that this from your you our ours are was were will would can could
    have has had not but all any who what when where which into onto than then them they
    their there here just also very more most some such only over under about after before
    near need needs looking find want wants get got like out off one two per via
    """.split()
)
# Words every service page uses; they say nothing about *which* service.
_GENERIC = frozenset(
    """
    service services repair repairs fix fixing help company companies local best top quality
    professional professionals team call today free estimate estimates quote quotes year years
    experience customer customers client clients business businesses home homes work works
    available fast affordable trusted reliable offer offers provide provides contact book booking
    """.split()
)
_NEED_RE = re.compile(
    r"\b(need(s|ed|ing)?|looking for|search(ing)? for|in need of|anyone (know|recommend|have)|"
    r"recommend(ation)?s?|can (someone|anyone)|who (can|does|do|fixes)|help( me| us)?|"
    r"broke(n)?|stopped working|not working|won'?t (start|turn on|work|heat|cool)|"
    r"asked for|asking for|quote for)\b",
    re.IGNORECASE,
)
_OFFERING_RE = re.compile(
    r"\b(we (offer|provide|specialize|install|repair|service|fix|do)|our (team|technicians|techs|"
    r"services|company|crew|experts)|call us|contact us|free (estimate|quote|consultation)s?|"
    r"licensed (and|&) insured|book (now|online|today)|serving .{0,40} since|"
    r"\d+\+? years (of experience|in business))\b",
    re.IGNORECASE,
)


def stem(word: str) -> str:
    word = word.casefold().strip("'")
    for suffix, replacement in (("ies", "y"), ("ing", ""), ("ers", ""), ("es", ""), ("ed", ""), ("er", ""), ("s", ""), ("e", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + replacement
    return word


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']{2,}|[.!?\n]")


def _terms(texts: Iterable[str]) -> frozenset[str]:
    """Distinctive word stems. A capitalized word inside a sentence is a
    proper name (a city, a brand) and says nothing about the service."""

    found: set[str] = set()
    for text in texts:
        sentence_start = True
        for token in _TOKEN_RE.findall(text):
            if token in ".!?\n":
                sentence_start = True
                continue
            proper = token[0].isupper() and not sentence_start
            sentence_start = False
            word = token.casefold()
            if proper or word in _STOPWORDS or word in _GENERIC:
                continue
            found.add(stem(word))
    return frozenset(term for term in found if len(term) >= 4)


@dataclass(frozen=True)
class SelectionProfile:
    """What the client sells and whom it serves, as word stems from its own materials."""

    service_terms: frozenset[str]
    audience_terms: frozenset[str]

    @classmethod
    def from_offer(
        cls, offer: OfferUnderstanding, materials: Sequence[DepositedMaterial] = ()
    ) -> "SelectionProfile":
        audience = _terms(claim.text for claim in offer.who_may_fit)
        service = _terms([*(claim.text for claim in offer.what_we_sell), *(m.body for m in materials)])
        return cls(service_terms=frozenset(service - audience), audience_terms=audience)


@dataclass(frozen=True)
class Selection:
    fit: float
    evidence: float
    addressable: bool
    accepted: bool
    why: str = ""


def select(
    text: str, profile: SelectionProfile, *, addressable: bool, source_kind: str = SOURCE_KIND_PERSON
) -> Selection:
    words = {stem(word) for word in _WORD_RE.findall(text.casefold())}
    service_hits = words & profile.service_terms
    audience_hits = words & profile.audience_terms
    need = bool(_NEED_RE.search(text))
    offering = bool(_OFFERING_RE.search(text))

    if source_kind == SOURCE_KIND_BUSINESS:
        # A company page: it must be the kind of business the client serves,
        # and it must not be selling the client's own service.
        if service_hits and (offering or not audience_hits):
            return Selection(0.0, 0.0, addressable, False, COMPETITOR)
        fit = 1.0 if audience_hits else 0.0
        evidence = 1.0 if audience_hits else 0.0
    else:
        if service_hits and offering and not need:
            return Selection(0.0, 0.0, addressable, False, COMPETITOR)
        fit = 1.0 if (service_hits or audience_hits) else 0.0
        evidence = 1.0 if (need and service_hits) or audience_hits else (0.5 if service_hits else 0.0)

    if fit == 0.0:
        return Selection(fit, evidence, addressable, False, NOT_TIED)
    if evidence == 0.0:
        return Selection(fit, evidence, addressable, False, NO_EVIDENCE)
    if not addressable:
        return Selection(fit, evidence, addressable, False, NOT_ADDRESSABLE)
    return Selection(fit, evidence, addressable, True)
