"""Handoff 1→2: a found person, not a message and not a booking."""

from __future__ import annotations

from dataclasses import dataclass

from evorove_lead.candidate import Candidate


def infer_channel(identity: str, explicit: str = "") -> str:
    if (explicit or "").strip():
        return explicit.strip()
    if "@" in identity:
        return "email"
    return "unknown"


@dataclass(frozen=True)
class Cycle1Handoff:
    """What cycle 2 may later use to write. This object does not send."""

    identity: str
    reason: str
    reason_source: str
    channel: str

    @classmethod
    def from_candidate(cls, candidate: Candidate, channel: str = "") -> Cycle1Handoff:
        return cls(
            identity=candidate.identity,
            reason=candidate.reason,
            reason_source=candidate.reason_source,
            channel=infer_channel(candidate.identity, channel),
        )
