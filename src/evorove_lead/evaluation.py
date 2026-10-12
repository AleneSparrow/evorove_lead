"""Synthetic qualification metrics. These numbers are not real-world accuracy.

Every fixture this module ships is labeled synthetic. A live pilot has to
score real search results separately before anyone quotes precision to a client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

SYNTHETIC = True
DATASET = "synthetic_engineering_fixtures"


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    expected_relevant: bool
    predicted_relevant: bool
    expected_duplicate: bool = False
    predicted_duplicate: bool = False
    evidence_quote: str = ""
    source_text: str = ""
    expected_class: str = ""
    predicted_class: str = ""


@dataclass(frozen=True)
class EvalReport:
    dataset: str
    synthetic: bool
    precision: float
    recall: float
    precision_at_k: float
    false_positive_rate: float
    duplicate_detection_accuracy: float
    evidence_validity: float
    qualification_consistency: float
    cost_usd: float | None
    elapsed_seconds: float
    support: int

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "synthetic": self.synthetic,
            "real_world_accuracy": False,
            "precision": self.precision,
            "recall": self.recall,
            "precision_at_k": self.precision_at_k,
            "false_positive_rate": self.false_positive_rate,
            "duplicate_detection_accuracy": self.duplicate_detection_accuracy,
            "evidence_validity": self.evidence_validity,
            "qualification_consistency": self.qualification_consistency,
            "cost_usd": self.cost_usd,
            "elapsed_seconds": self.elapsed_seconds,
            "support": self.support,
        }


def evaluate(
    cases: list[EvalCase],
    *,
    k: int = 5,
    cost_usd: float | None = None,
    repeat_predictions: list[str] | None = None,
) -> EvalReport:
    started = time.perf_counter()
    tp = fp = fn = tn = 0
    for case in cases:
        if case.predicted_relevant and case.expected_relevant:
            tp += 1
        elif case.predicted_relevant and not case.expected_relevant:
            fp += 1
        elif not case.predicted_relevant and case.expected_relevant:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    ranked = [case for case in cases if case.predicted_relevant][:k]
    precision_at_k = (
        sum(1 for case in ranked if case.expected_relevant) / len(ranked) if ranked else 0.0
    )
    duplicates = [case for case in cases if case.expected_duplicate or case.predicted_duplicate]
    if duplicates:
        duplicate_accuracy = sum(
            1 for case in duplicates if case.expected_duplicate == case.predicted_duplicate
        ) / len(duplicates)
    else:
        duplicate_accuracy = 1.0
    quoted = [case for case in cases if case.predicted_relevant]
    if quoted:
        evidence_validity = sum(
            1
            for case in quoted
            if case.evidence_quote and case.evidence_quote in case.source_text
        ) / len(quoted)
    else:
        evidence_validity = 1.0
    if repeat_predictions is None:
        consistency = sum(
            1 for case in cases if not case.expected_class or case.expected_class == case.predicted_class
        ) / len(cases) if cases else 1.0
    else:
        consistency = (
            sum(1 for case, repeat in zip(cases, repeat_predictions) if case.predicted_class == repeat)
            / len(cases)
            if cases
            else 1.0
        )
    elapsed = time.perf_counter() - started
    return EvalReport(
        dataset=DATASET,
        synthetic=SYNTHETIC,
        precision=precision,
        recall=recall,
        precision_at_k=precision_at_k,
        false_positive_rate=fpr,
        duplicate_detection_accuracy=duplicate_accuracy,
        evidence_validity=evidence_validity,
        qualification_consistency=consistency,
        cost_usd=cost_usd,
        elapsed_seconds=elapsed,
        support=len(cases),
    )
