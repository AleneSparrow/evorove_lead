"""Rank qualified opportunities from measured signals. Missing value stays missing."""

from __future__ import annotations

from evorove_lead.selection import (
    EXPLICIT_INTENT,
    ICP_MATCH,
    INFERRED_OPPORTUNITY,
    INSUFFICIENT_EVIDENCE,
    IRRELEVANT,
)

_CLASS_WEIGHT = {
    EXPLICIT_INTENT: 1.0,
    INFERRED_OPPORTUNITY: 0.7,
    ICP_MATCH: 0.45,
    INSUFFICIENT_EVIDENCE: 0.0,
    IRRELEVANT: 0.0,
}
_CONFIDENCE = {
    EXPLICIT_INTENT: 1.0,
    INFERRED_OPPORTUNITY: 0.6,
    ICP_MATCH: 0.8,
    INSUFFICIENT_EVIDENCE: 0.2,
    IRRELEVANT: 0.9,
}


def rank_score(
    *,
    evidence_class: str,
    fit: float,
    evidence: float,
    age_days: int | None = None,
) -> float:
    """Higher is better. `age_days` None means the source had no date; recency is not invented."""

    kind = evidence_class or IRRELEVANT
    if age_days is None:
        recency = 0.5
    else:
        recency = max(0.0, 1.0 - (max(0, age_days) / 548))
    return (
        0.30 * _clamp(fit)
        + 0.25 * _clamp(evidence)
        + 0.25 * _CLASS_WEIGHT.get(kind, 0.0)
        + 0.10 * recency
        + 0.10 * _CONFIDENCE.get(kind, 0.0)
    )


def _clamp(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value
