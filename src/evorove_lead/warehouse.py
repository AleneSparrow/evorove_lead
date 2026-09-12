"""Analysis warehouse port. The engine writes here, never straight to CRM.

`docs/cycle-1-contract.md` calls this "the warehouse": briefs, raw traces,
rejections, and scores, tenant-scoped by `business_id`. This module defines
the records and the protocol; `sqlalchemy_warehouse.py` is the only place
that touches SQLAlchemy. Nothing here sends a message or books a slot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class BriefRecord:
    id: str
    business_id: str
    what_we_sell: tuple[dict[str, str], ...]
    who_may_fit: tuple[dict[str, str], ...]
    commercial_claims: tuple[dict[str, str], ...]
    must_not_promise: tuple[str, ...]
    created_at: datetime
    # Owner-set, opaque (see BusinessSeed.business_archetype). Stored here
    # so reweight_hypotheses.py can look it up by business_id alone,
    # without needing the archetype re-supplied on every job run.
    business_archetype: str = ""


@dataclass(frozen=True)
class HypothesisRecord:
    id: str
    business_id: str
    brief_id: str
    audience_segment: str
    channel: str
    query_template: str
    intent_trigger: str
    status: str  # "live" | "dead" | "paused"
    fit_score: float
    evidence_score: float
    reach_estimate: int
    created_at: datetime
    # Phase 4 (reweight_hypotheses.py fills these in; the engine leaves
    # them at their defaults): share of this hypothesis's candidates that
    # reached Cold, share of those that later reached Done, and how many
    # probe rounds the next run should spend here.
    accept_rate: float = 0.0
    close_rate: float = 0.0
    query_budget: int = 1


@dataclass(frozen=True)
class TraceRecord:
    id: str
    business_id: str
    hypothesis_id: str
    url: str
    fetched_at: datetime
    raw_text: str
    query_used: str
    source_channel: str
    language: str = ""
    geo_hint: str = ""


@dataclass(frozen=True)
class RejectedTraceRecord:
    id: str
    business_id: str
    reason: str
    rejected_at: datetime
    trace_id: str = ""


@dataclass(frozen=True)
class CandidateRecord:
    id: str
    business_id: str
    hypothesis_id: str
    identity: str
    channel: str
    reason: str
    reason_source: str
    fit: float
    evidence: float
    addressable: bool
    decision: str  # "cold" | "rejected"
    decided_at: datetime
    trace_id: str = ""
    email: str = ""
    phone: str = ""


@dataclass(frozen=True)
class HypothesisOutcomeRecord:
    id: str
    business_id: str
    hypothesis_id: str
    case_id: str
    outcome: str  # "done" | "dropped" | "offer_made" | "in_progress"
    recorded_at: datetime


class AnalysisWarehouse(Protocol):
    """Tenant-scoped store for cycle 1's own analysis, separate from CRM."""

    def save_brief(self, record: BriefRecord) -> None: ...

    def list_briefs(self, business_id: str) -> Sequence[BriefRecord]: ...

    def save_hypothesis(self, record: HypothesisRecord) -> None: ...

    def list_hypotheses(self, business_id: str) -> Sequence[HypothesisRecord]: ...

    def save_trace(self, record: TraceRecord) -> None: ...

    def list_traces(self, business_id: str, hypothesis_id: str) -> Sequence[TraceRecord]: ...

    def save_rejected_trace(self, record: RejectedTraceRecord) -> None: ...

    def list_rejected_traces(self, business_id: str) -> Sequence[RejectedTraceRecord]: ...

    def save_candidate(self, record: CandidateRecord) -> None: ...

    def list_candidates(
        self, business_id: str, hypothesis_id: str
    ) -> Sequence[CandidateRecord]: ...

    def save_hypothesis_outcome(self, record: HypothesisOutcomeRecord) -> None: ...

    def list_hypothesis_outcomes(
        self, business_id: str, hypothesis_id: str
    ) -> Sequence[HypothesisOutcomeRecord]: ...


class NullAnalysisWarehouse:
    """Default warehouse: no `DATABASE_URL`, nothing is written. Reads are empty."""

    def save_brief(self, record: BriefRecord) -> None:
        return None

    def list_briefs(self, business_id: str) -> Sequence[BriefRecord]:
        return ()

    def save_hypothesis(self, record: HypothesisRecord) -> None:
        return None

    def list_hypotheses(self, business_id: str) -> Sequence[HypothesisRecord]:
        return ()

    def save_trace(self, record: TraceRecord) -> None:
        return None

    def list_traces(self, business_id: str, hypothesis_id: str) -> Sequence[TraceRecord]:
        return ()

    def save_rejected_trace(self, record: RejectedTraceRecord) -> None:
        return None

    def list_rejected_traces(self, business_id: str) -> Sequence[RejectedTraceRecord]:
        return ()

    def save_candidate(self, record: CandidateRecord) -> None:
        return None

    def list_candidates(
        self, business_id: str, hypothesis_id: str
    ) -> Sequence[CandidateRecord]:
        return ()

    def save_hypothesis_outcome(self, record: HypothesisOutcomeRecord) -> None:
        return None

    def list_hypothesis_outcomes(
        self, business_id: str, hypothesis_id: str
    ) -> Sequence[HypothesisOutcomeRecord]:
        return ()


def _upsert(records: list, record) -> None:
    """Replace the row with the same `.id`, or append. Mirrors `session.merge`."""

    for index, existing in enumerate(records):
        if existing.id == record.id:
            records[index] = record
            return
    records.append(record)


class RecordingAnalysisWarehouse:
    """In-memory warehouse for tests. Records what the engine would have written."""

    def __init__(self) -> None:
        self.briefs: list[BriefRecord] = []
        self.hypotheses: list[HypothesisRecord] = []
        self.traces: list[TraceRecord] = []
        self.rejected_traces: list[RejectedTraceRecord] = []
        self.candidates: list[CandidateRecord] = []
        self.hypothesis_outcomes: list[HypothesisOutcomeRecord] = []

    def save_brief(self, record: BriefRecord) -> None:
        _upsert(self.briefs, record)

    def list_briefs(self, business_id: str) -> Sequence[BriefRecord]:
        return tuple(r for r in self.briefs if r.business_id == business_id)

    def save_hypothesis(self, record: HypothesisRecord) -> None:
        _upsert(self.hypotheses, record)

    def list_hypotheses(self, business_id: str) -> Sequence[HypothesisRecord]:
        return tuple(r for r in self.hypotheses if r.business_id == business_id)

    def save_trace(self, record: TraceRecord) -> None:
        self.traces.append(record)

    def list_traces(self, business_id: str, hypothesis_id: str) -> Sequence[TraceRecord]:
        return tuple(
            r
            for r in self.traces
            if r.business_id == business_id and r.hypothesis_id == hypothesis_id
        )

    def save_rejected_trace(self, record: RejectedTraceRecord) -> None:
        self.rejected_traces.append(record)

    def list_rejected_traces(self, business_id: str) -> Sequence[RejectedTraceRecord]:
        return tuple(r for r in self.rejected_traces if r.business_id == business_id)

    def save_candidate(self, record: CandidateRecord) -> None:
        _upsert(self.candidates, record)

    def list_candidates(
        self, business_id: str, hypothesis_id: str
    ) -> Sequence[CandidateRecord]:
        return tuple(
            r
            for r in self.candidates
            if r.business_id == business_id and r.hypothesis_id == hypothesis_id
        )

    def save_hypothesis_outcome(self, record: HypothesisOutcomeRecord) -> None:
        self.hypothesis_outcomes.append(record)

    def list_hypothesis_outcomes(
        self, business_id: str, hypothesis_id: str
    ) -> Sequence[HypothesisOutcomeRecord]:
        return tuple(
            r
            for r in self.hypothesis_outcomes
            if r.business_id == business_id and r.hypothesis_id == hypothesis_id
        )
