"""Module 3: the named decision-maker's contact, not the first address on the page.

`web_people_search.pick_contact_email(business=True)` accepts any address
on the company's own domain -- including `info@`, which the plan is
explicit is not good enough: "не первый попавшийся email, а контакт именно
того, кто принимает решения (владелец/основатель/директор)".

This is the heuristic half of that: a name that sits next to an
owner/founder/director-shaped role word, on the homepage or a guessed
About/Team page, paired with the email on that same page that looks like
it belongs to that name (never a generic business inbox). When no such
pair turns up anywhere, this returns None and the caller falls back to
`pick_contact_email`'s wider rule. `decision_maker_llm.py` is the other
half: an LLM reading of the same fetched pages, for an unstructured About
page this regex heuristic cannot parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urljoin

from evorove_lead.crm_touch import EMAIL_RE
from evorove_lead.platforms import registrable_domain
from evorove_lead.presence import PresenceRejected

_ROLE_RE = re.compile(
    r"\b(co-founder|founder|co-owner|owner|ceo|president|managing partner|"
    r"proprietor|principal|director)\b",
    re.IGNORECASE,
)
_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b")
_NAME_STOPWORDS = frozenset(
    (
        "contact us", "about us", "our team", "meet team", "meet the team",
        "read more", "learn more", "get started", "privacy policy",
        "terms service", "terms of service", "all rights", "all rights reserved",
    )
)
# "Our Director, Jane Doe, ..." and "Director Jane Doe" both satisfy the
# bare two-capitalized-words shape `_NAME_RE` looks for. Reject a match
# that leads with a determiner/pronoun or with the role word itself, so
# the search moves on to the real name later in the same sentence.
_NAME_LEADING_STOPWORDS = frozenset(
    "our the my your this a an read meet contact about get learn".split()
)
# Common slugs a small-business site uses for its About/Team page. This is a
# guess, not a discovered link -- a page whose slug is not on this list is
# simply not found by this heuristic.
_TEAM_PATH_SLUGS = ("about", "about-us", "team", "our-team")
_GENERIC_LOCAL_PARTS = frozenset(
    "info support sales contact hello office admin team enquiries inquiries "
    "help general".split()
)


@dataclass(frozen=True)
class DecisionMaker:
    name: str
    role: str
    email: str
    evidence_quote: str
    source_url: str


def fetch_candidate_pages(
    company_url: str, fetch_text: Callable[[str], str]
) -> tuple[tuple[str, str], ...]:
    """The homepage plus each guessed About/Team page that actually fetched.

    Exposed separately from the heuristic so a caller can also hand these
    same pages to the LLM fallback (`decision_maker_llm.py`) without
    fetching them a second time.
    """

    pages: list[tuple[str, str]] = []
    for url in _candidate_urls(company_url):
        try:
            text = fetch_text(url)
        except PresenceRejected:
            continue
        pages.append((url, text))
    return tuple(pages)


def heuristic_decision_maker(pages: Sequence[tuple[str, str]], domain: str) -> DecisionMaker | None:
    """Stops at the first page where a name sits near a role word AND that
    page also has an email that looks like it belongs to that name."""

    for url, text in pages:
        found = _find_named_role(text)
        if found is None:
            continue
        name, role, sentence = found
        email = _email_for_name(name, domain, text)
        if email is None:
            continue
        return DecisionMaker(name=name, role=role, email=email, evidence_quote=sentence, source_url=url)
    return None


def find_decision_maker(
    company_url: str, domain: str, fetch_text: Callable[[str], str]
) -> DecisionMaker | None:
    """Convenience wrapper: fetch the candidate pages, then the regex heuristic only."""

    return heuristic_decision_maker(fetch_candidate_pages(company_url, fetch_text), domain)


def _candidate_urls(company_url: str) -> tuple[str, ...]:
    guesses = [urljoin(company_url, f"/{slug}") for slug in _TEAM_PATH_SLUGS]
    ordered = [company_url, *guesses]
    seen: set[str] = set()
    unique: list[str] = []
    for url in ordered:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return tuple(unique)


def _find_named_role(text: str) -> tuple[str, str, str] | None:
    """First (name, role, sentence) where a name and a role word co-occur."""

    for raw_sentence in text.split("."):
        sentence = raw_sentence.strip()
        if not sentence:
            continue
        role_match = _ROLE_RE.search(sentence)
        if role_match is None:
            continue
        for name_match in _NAME_RE.finditer(sentence):
            # "Our Director Maria Alvarez" matches as one 4-word run; drop a
            # leading determiner/pronoun or the role word itself rather than
            # discarding the whole match, so "Maria Alvarez" still surfaces.
            words = name_match.group(0).split()
            while words and (
                words[0].casefold() in _NAME_LEADING_STOPWORDS or _ROLE_RE.fullmatch(words[0])
            ):
                words.pop(0)
            if len(words) < 2:
                continue
            name = " ".join(words)
            if name.casefold() in _NAME_STOPWORDS:
                continue
            return name, role_match.group(0), sentence
    return None


def _email_for_name(name: str, domain: str, page_text: str) -> str | None:
    """The address on this page that looks like this name's, or the page's
    only non-generic address on the company's own domain as a fallback --
    a Team/About bio page usually names just the one person anyway."""

    words = [word.casefold() for word in name.split() if word]
    if not words:
        return None
    first, last = words[0], words[-1]
    fallback: str | None = None
    for match in EMAIL_RE.finditer(page_text):
        address = match.group(0).casefold().strip(".")
        local, _, email_domain = address.partition("@")
        if registrable_domain(email_domain) != domain:
            continue
        if local in _GENERIC_LOCAL_PARTS:
            continue
        if first in local or last in local:
            return address
        if fallback is None:
            fallback = address
    return fallback
