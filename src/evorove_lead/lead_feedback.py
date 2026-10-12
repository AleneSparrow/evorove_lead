"""Bounded feedback. One event moves one hypothesis budget by at most one step.

Nothing here retrains a model or rewrites every search hypothesis.
"""

from __future__ import annotations

from datetime import datetime, timezone

from evorove_lead.reweight_hypotheses import MAX_QUERY_BUDGET
from evorove_lead.warehouse import FeedbackRecord, new_id

FEEDBACK_KINDS = frozenset(
    {
        "relevant",
        "irrelevant",
        "incorrect_classification",
        "duplicate",
        "outdated",
        "incorrect_icp",
        "insufficient_evidence",
    }
)
_DECREASE = frozenset({"irrelevant", "incorrect_icp", "outdated"})
_INCREASE = frozenset({"relevant"})


def apply_feedback(warehouse, *, business_id: str, qualification_id: str, kind: str, now: datetime | None = None) -> FeedbackRecord:
    if kind not in FEEDBACK_KINDS:
        raise ValueError(f"unknown feedback kind {kind!r}")
    moment = now or datetime.now(timezone.utc)
    qualification = warehouse.get_qualification(business_id, qualification_id)
    hypothesis_id = ""
    query_template = ""
    delta = 0
    if qualification is not None:
        hypothesis_id = qualification.hypothesis_id
        query_template = qualification.query_template
        if kind in _DECREASE:
            delta = -1
        elif kind in _INCREASE:
            delta = 1
    if delta and hypothesis_id:
        for hypothesis in warehouse.list_hypotheses(business_id):
            if hypothesis.id != hypothesis_id:
                continue
            updated = min(MAX_QUERY_BUDGET, max(0, hypothesis.query_budget + delta))
            delta = updated - hypothesis.query_budget
            if delta:
                warehouse.save_hypothesis(
                    hypothesis.__class__(
                        **{**hypothesis.__dict__, "query_budget": updated}
                    )
                )
            break
        else:
            delta = 0
    record = FeedbackRecord(
        id=new_id("feedback"),
        business_id=business_id,
        qualification_id=qualification_id,
        hypothesis_id=hypothesis_id,
        query_template=query_template,
        kind=kind,
        budget_delta=delta,
        created_at=moment,
    )
    warehouse.save_feedback(record)
    return record
