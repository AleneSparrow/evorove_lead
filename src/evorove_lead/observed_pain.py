"""Conservative, extractive operational pain. No model calls or new sources.

A sentence must state both a supported operational failure and its cost.
Only capabilities explicitly present in what_we_sell authorize a match.
Unrecognized offers/problems deliberately produce no pain. This is not a
semantic classifier for arbitrary businesses. Pain is copied, never paraphrased.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from evorove_lead.candidate import Candidate
from evorove_lead.offer import OfferUnderstanding

# Capability, observed failure, observed business cost. Industry independent.
_RULES = (
    (
        r"\b(?:automated? (?:replies|responses)|respond(?:s|ing)? to (?:leads|customers|requests)|answer(?:s|ing)? (?:leads|customers|requests)|customer communication|sales conversations)\b",
        r"\b(?:requests|inquiries|enquiries|leads|customers)\b.{0,65}\b(?:go unanswered|are unanswered|wait until|receive no reply|are not answered)\b",
        r"\b(?:customers|people|prospects)\b.{0,50}\b(?:book elsewhere|book other companies|leave for competitors|go elsewhere)\b",
    ),
    (
        r"\blead generation\b",
        r"\b(?:company|business|team)\b.{0,65}\b(?:cannot find|struggles to find|lacks|has no)\b.{0,35}\b(?:leads|prospects|customers)\b",
        r"\b(?:sales|pipeline|revenue)\b.{0,45}\b(?:stalled|shrinking|declining|empty)\b",
    ),
    (
        r"\b(?:automate[sd]? data entry|data entry automation|automated data entry)\b",
        r"\b(?:staff|team|employees)\b.{0,70}\b(?:manually (?:copy|enter|transfer)|copy .{0,30} manually)\b",
        r"\b(?:hours|time)\b.{0,30}\b(?:each day|every day|each week|every week|wasted|lost)\b",
    ),
)
# These sentences are not safe factual third-person observations of operations.
_UNSAFE = re.compile(
    r"\b(?:I|we|our|you|your|my|if|might|may|could|would|should|perhaps|"
    r"hypothetical|example|not|never|no longer|health|medical|illness|family|"
    r"marriage|divorce|debt|salary|personal|patients|cancer|pregnancy|religion|sexual)\b", re.I
)


def _grounded(text: str) -> bool:
    # Same floor as cycle 2's _has_grounded_reason; no cross-repo import.
    return (len(text) >= 16 and len(text.split()) >= 3
            and sum(c.isalpha() for c in text) >= 10)


def validate_pain(*, pain: str, excerpt: str, source_url: str,
                  source_text: str, offer: OfferUnderstanding) -> str:
    """Fail closed; a capability assertion from a model would not authorize pain.

    This initial slice additionally requires the note to equal its evidence.
    Any future AI proposal must pass this server check, not bypass it.
    """
    if not all(isinstance(x, str) for x in (pain, excerpt, source_url, source_text)):
        return ""
    pain = pain.strip()
    if (not excerpt or excerpt not in source_text or pain != excerpt
            or not source_url.startswith(("https://", "http://"))
            or not _grounded(pain) or len(pain) > 500 or _UNSAFE.search(pain)
            or not re.fullmatch(r"[A-Z][\x20-\x7e]+[.!?]", pain)):
        return ""
    capabilities = " ".join(c.text for c in offer.what_we_sell
                            if not re.search(r"\b(?:no|not|without|never|cannot)\b", c.text, re.I))
    for capability, failure, cost in _RULES:
        if (re.search(capability, capabilities, re.I)
                and re.search(failure, pain, re.I) and re.search(cost, pain, re.I)):
            return pain
    return ""


def attach_observed_pain(candidate: Candidate, offer: OfferUnderstanding,
                         sources: Sequence[tuple[str, str]]) -> Candidate:
    """Sources are candidate-specific stored material, never the client's site."""
    candidate = replace(candidate, observed_pain="", pain_evidence_excerpt="", pain_evidence_source="")
    for url, text in sources:
        # One complete sentence only; exact substring, including internal whitespace.
        for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", text):
            excerpt = sentence.strip()
            if excerpt not in candidate.reason:
                continue  # Do not promote unrelated problems elsewhere on the page.
            pain = validate_pain(pain=excerpt, excerpt=excerpt, source_url=url,
                                 source_text=text, offer=offer)
            if pain:
                return replace(candidate, observed_pain=pain, pain_evidence_excerpt=excerpt,
                               pain_evidence_source=url)
    return candidate


def pain_counts(candidates: Sequence[Candidate]) -> dict[str, int | float | None]:
    with_pain = sum(bool(c.observed_pain) for c in candidates)
    return {"candidates_with_pain": with_pain,
            "candidates_without_pain": len(candidates) - with_pain,
            "pain_ratio": with_pain / len(candidates) if candidates else None}
