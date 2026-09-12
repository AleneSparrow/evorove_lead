"""System-wide (NOT tenant-scoped) library of abstract hypothesis patterns.

`business_archetype -> channel_family -> query_pattern -> observed_close_rate_band`.
No contact, no trace text, no business or person identifier -- ever.
`query_pattern` here is a category (an `IntentTrigger.kind` like
"need_statement"), never a hypothesis's literal `query_template`, because
that text can quote the owner's own service wording and would identify
the business it came from.

This is a separate port from `AnalysisWarehouse` on purpose: every
`AnalysisWarehouse` method takes a `business_id` because everything it
touches is tenant data. Nothing here does, because nothing here is.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

CLOSE_RATE_BANDS = ("low", "medium", "high")
LOW_BAND_MAX = 0.1
MEDIUM_BAND_MAX = 0.3


def new_pattern_id() -> str:
    return f"pattern_{uuid.uuid4().hex}"


def close_rate_band(close_rate: float) -> str:
    """Bucket a measured close_rate into the same bands `reweight_hypotheses`
    uses to pause/boost, so a pattern's band means the same thing tenant-side."""

    if close_rate < LOW_BAND_MAX:
        return "low"
    if close_rate < MEDIUM_BAND_MAX:
        return "medium"
    return "high"


@dataclass(frozen=True)
class PatternObservation:
    business_archetype: str
    channel_family: str
    query_pattern: str
    observed_close_rate_band: str
    sample_size: int
    updated_at: datetime


class PatternLibrary(Protocol):
    def record_observation(
        self,
        *,
        business_archetype: str,
        channel_family: str,
        query_pattern: str,
        close_rate: float,
        sample_size: int,
    ) -> None:
        """Fold one business's measured close_rate into the system-wide pattern."""

    def suggest_patterns(self, business_archetype: str) -> Sequence[PatternObservation]:
        """Known patterns for this archetype, best band first. Empty for a new archetype."""


class NullPatternLibrary:
    """Default: no patterns recorded, none suggested. A new archetype starts from zero."""

    def record_observation(
        self,
        *,
        business_archetype: str,
        channel_family: str,
        query_pattern: str,
        close_rate: float,
        sample_size: int,
    ) -> None:
        return None

    def suggest_patterns(self, business_archetype: str) -> Sequence[PatternObservation]:
        return ()


class RecordingPatternLibrary:
    """In-memory library for tests."""

    def __init__(self) -> None:
        self.observations: dict[tuple[str, str, str], PatternObservation] = {}

    def record_observation(
        self,
        *,
        business_archetype: str,
        channel_family: str,
        query_pattern: str,
        close_rate: float,
        sample_size: int,
    ) -> None:
        from datetime import timezone

        key = (business_archetype, channel_family, query_pattern)
        self.observations[key] = PatternObservation(
            business_archetype=business_archetype,
            channel_family=channel_family,
            query_pattern=query_pattern,
            observed_close_rate_band=close_rate_band(close_rate),
            sample_size=sample_size,
            updated_at=datetime.now(timezone.utc),
        )

    def suggest_patterns(self, business_archetype: str) -> Sequence[PatternObservation]:
        band_rank = {"high": 0, "medium": 1, "low": 2}
        matches = [o for o in self.observations.values() if o.business_archetype == business_archetype]
        return tuple(sorted(matches, key=lambda o: band_rank[o.observed_close_rate_band]))
