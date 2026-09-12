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
    """What cycle 2 may later use to write. This object does not send.

    `hypothesis_id` is metadata, not a new PII store: it lets a later,
    non-PII outcome event (phase 3 -- Done/dropped in cycle 2/3) find its
    way back to the hypothesis that produced this person, without cycle 1
    ever seeing what happened to them personally.
    """

    identity: str
    reason: str
    reason_source: str
    channel: str
    person_id: str = ""
    hypothesis_id: str = ""

    @classmethod
    def from_candidate(
        cls,
        candidate: Candidate,
        channel: str = "",
        person_id: str = "",
        hypothesis_id: str = "",
    ) -> Cycle1Handoff:
        return cls(
            identity=candidate.identity,
            reason=candidate.reason,
            reason_source=candidate.reason_source,
            channel=infer_channel(candidate.identity, channel),
            person_id=person_id,
            hypothesis_id=hypothesis_id,
        )
