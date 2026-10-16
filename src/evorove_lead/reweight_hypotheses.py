"""Job: recompute each hypothesis's accept_rate/close_rate and next-run budget.

Not part of `LeadGenerationEngine`'s per-run path. Run this on a schedule
(cron / task runner), one business at a time, after enough runs have
accumulated candidates and outcomes. Reads and writes the warehouse only --
never touches CRM, the open web, or a person's identity.

No magic weights: a hypothesis's budget only ever moves by one step per
run, and only once there is enough sample size (`MIN_SAMPLE_FOR_RATING`)
to trust the signal -- both numbers come straight out of `verify_hypothesis`
and `accept_hit`'s own real decisions, recorded as `candidates` and
`hypothesis_outcomes` rows.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from typing import Sequence

from evorove_lead.pattern_library import PatternLibrary
from evorove_lead.warehouse import AnalysisWarehouse, HypothesisRecord

# "За N прогонов" from the contract: don't pause or boost a hypothesis on
# one lucky or unlucky case -- wait for enough Cold candidates to trust the
# close_rate. Also gates when a close_rate is trusted enough to fold into
# the system-wide, cross-tenant pattern library.
MIN_SAMPLE_FOR_RATING = 30
CLOSE_RATE_PAUSE_THRESHOLD = 0.1
CLOSE_RATE_BOOST_THRESHOLD = 0.3
MIN_QUERY_BUDGET = 0
MAX_QUERY_BUDGET = 5


@dataclass(frozen=True)
class HypothesisReweight:
    """What changed for one hypothesis, for logging -- not stored separately."""

    hypothesis_id: str
    accept_rate: float
    close_rate: float
    sample_size: int
    previous_status: str
    new_status: str
    previous_budget: int
    new_budget: int


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def compute_rates(
    warehouse: AnalysisWarehouse, business_id: str, hypothesis: HypothesisRecord
) -> tuple[float, float, int]:
    """accept_rate = candidates that reached Cold / all candidates.

    close_rate = distinct cases with a "done" outcome / Cold candidates.
    `sample_size` is the Cold candidate count -- the base close_rate is
    measured against.
    """

    candidates = warehouse.list_candidates(business_id, hypothesis.id)
    cold = [c for c in candidates if c.decision == "cold"]
    accept_rate = _rate(len(cold), len(candidates))

    outcomes = warehouse.list_hypothesis_outcomes(business_id, hypothesis.id)
    done_cases = {o.case_id for o in outcomes if o.outcome == "done"}
    close_rate = _rate(len(done_cases), len(cold))
    return accept_rate, close_rate, len(cold)


def _next_status_and_budget(
    *, current_status: str, close_rate: float, sample_size: int, current_budget: int
) -> tuple[str, int]:
    if current_status == "dead":
        # verify_hypothesis already killed this one on measured reach.
        # Reweighting doesn't resurrect a hypothesis that found no one.
        return current_status, MIN_QUERY_BUDGET
    if sample_size < MIN_SAMPLE_FOR_RATING:
        return current_status, current_budget
    if close_rate < CLOSE_RATE_PAUSE_THRESHOLD:
        return "paused", MIN_QUERY_BUDGET
    if close_rate >= CLOSE_RATE_BOOST_THRESHOLD:
        return "live", min(current_budget + 1, MAX_QUERY_BUDGET)
    return "live", current_budget


def latest_business_archetype(warehouse: AnalysisWarehouse, business_id: str) -> str:
    """The most recently stored, owner-set archetype for this business, or "".

    Briefs accumulate history (never overwritten); the archetype can also
    change between brief snapshots if the owner corrects it, so this reads
    the latest one rather than the first.
    """

    briefs = warehouse.list_briefs(business_id)
    for brief in reversed(briefs):
        if brief.business_archetype.strip():
            return brief.business_archetype.strip()
    return ""


def reweight_hypotheses(
    warehouse: AnalysisWarehouse,
    business_id: str,
    *,
    pattern_library: PatternLibrary | None = None,
    business_archetype: str = "",
    now=None,
) -> tuple[HypothesisReweight, ...]:
    """Recompute and persist accept_rate/close_rate/status/budget for one business.

    When `pattern_library` and `business_archetype` are given, every
    hypothesis with enough sample size also folds its close_rate into the
    system-wide pattern library (`channel` + `intent_trigger.kind` only --
    never the literal `query_template`, which can quote this business's
    own service wording). This is the only place that ever writes to the
    pattern library from tenant data; it never reads a business_id back.
    """

    results: list[HypothesisReweight] = []
    for hypothesis in warehouse.list_hypotheses(business_id):
        accept_rate, close_rate, sample_size = compute_rates(warehouse, business_id, hypothesis)
        mature = mature_performance(warehouse, business_id, hypothesis.id, now=now)
        effective_sample = mature.resolved if mature.cohort_mature and mature.attribution_complete else 0
        new_status, new_budget = _next_status_and_budget(
            current_status=hypothesis.status,
            close_rate=mature.rate if mature.rate is not None else 0,
            sample_size=effective_sample,
            current_budget=hypothesis.query_budget,
        )
        warehouse.save_hypothesis(
            replace(
                hypothesis,
                accept_rate=accept_rate,
                close_rate=close_rate,
                status=new_status,
                query_budget=new_budget,
            )
        )
        results.append(
            HypothesisReweight(
                hypothesis_id=hypothesis.id,
                accept_rate=accept_rate,
                close_rate=close_rate,
                sample_size=sample_size,
                previous_status=hypothesis.status,
                new_status=new_status,
                previous_budget=hypothesis.query_budget,
                new_budget=new_budget,
            )
        )
    if pattern_library is not None and business_archetype.strip():
        publish_mature_patterns(warehouse, business_id, pattern_library, business_archetype, now=now)
    return tuple(results)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from evorove_lead.sqlalchemy_pattern_library import pattern_library_from_env
    from evorove_lead.sqlalchemy_warehouse import warehouse_from_env

    parser = argparse.ArgumentParser(
        description="Recompute accept_rate/close_rate and next-run query_budget for one business's hypotheses."
    )
    parser.add_argument("business_id")
    parser.add_argument(
        "--archetype",
        default="",
        help="Business archetype (e.g. 'local_service_appointment'). Defaults "
        "to whatever the owner set on this business's latest brief, if any. "
        "When one is known either way, also folds results into the "
        "system-wide pattern library.",
    )
    args = parser.parse_args(argv)

    warehouse = warehouse_from_env()
    archetype = args.archetype.strip() or latest_business_archetype(warehouse, args.business_id)
    results = reweight_hypotheses(
        warehouse,
        args.business_id,
        pattern_library=pattern_library_from_env() if archetype else None,
        business_archetype=archetype,
    )
    for result in results:
        print(
            f"{result.hypothesis_id}: accept_rate={result.accept_rate:.2f} "
            f"close_rate={result.close_rate:.2f} n={result.sample_size} "
            f"status {result.previous_status}->{result.new_status} "
            f"budget {result.previous_budget}->{result.new_budget}"
        )
    return 0




@dataclass(frozen=True)
class MaturePerformance:
    cold: int
    done: int
    dropped: int
    pending: int
    cohort_mature: bool
    attribution_complete: bool

    @property
    def resolved(self): return self.done + self.dropped

    @property
    def rate(self): return self.done / self.resolved if self.resolved else None


def mature_performance(warehouse, business_id, hypothesis_id, *, now=None, maturity_days=30):
    """Reception timestamps only: the external event contract has no occurred_at.

    Keep unresolved cases pending. A cohort is not penalized before its entire
    observation window closes. Inconsistent counts disable adaptation.
    """
    from datetime import datetime, timezone, timedelta
    now = now or datetime.now(timezone.utc)
    cold = [c for c in warehouse.list_candidates(business_id, hypothesis_id) if c.decision == "cold"]
    latest = {}
    for event in warehouse.list_hypothesis_outcomes(business_id, hypothesis_id):
        existing = latest.get(event.case_id)
        if existing is None or event.recorded_at > existing.recorded_at:
            latest[event.case_id] = event
    done = sum(e.outcome == "done" for e in latest.values())
    dropped = sum(e.outcome == "dropped" for e in latest.values())
    def aware(t): return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    mature = bool(cold) and all(now - aware(c.decided_at) >= timedelta(days=maturity_days) for c in cold)
    complete = len(latest) <= len(cold)
    return MaturePerformance(len(cold), done, dropped, max(0, len(cold)-done-dropped), mature, complete)


def strategy_feedback(warehouse, store, business_id, *, now=None):
    """Join immutable per-run hypotheses to stable ICP strategies, never to PII."""
    totals = {}
    for link in store.list(business_id, "hypothesis_attribution"):
        performance = mature_performance(warehouse, business_id, link["hypothesis_id"], now=now)
        if not performance.cohort_mature or not performance.attribution_complete: continue
        total = totals.setdefault(link["stable_key"], {"done": 0, "resolved": 0})
        total["done"] += performance.done
        total["resolved"] += performance.resolved
    return totals


def publish_mature_patterns(warehouse, business_id, library, archetype, *, now=None):
    from evorove_lead.evidence import digest
    groups = {}
    for hypothesis in warehouse.list_hypotheses(business_id):
        perf = mature_performance(warehouse, business_id, hypothesis.id, now=now)
        if not perf.cohort_mature or not perf.attribution_complete: continue
        key = (hypothesis.channel, hypothesis.intent_trigger)
        group = groups.setdefault(key, {"done": 0, "resolved": 0})
        group["done"] += perf.done
        group["resolved"] += perf.resolved
    for (channel, trigger), counts in groups.items():
        if hasattr(warehouse, "database_engine"):
            from evorove_lead.intelligence_store import IntelligenceStore, ResearchDocumentRow
            from sqlalchemy import select
            from sqlalchemy.orm import Session
            store = IntelligenceStore(warehouse.database_engine)
            key = digest(archetype + channel + trigger)
            store.save(business_id, "pattern_contribution", key, {**counts, "archetype": archetype, "channel": channel, "trigger": trigger})
            # Only anonymized sufficient statistics leave the tenant store.
            with Session(warehouse.database_engine) as session:
                values = [row.payload for row in session.scalars(select(ResearchDocumentRow).where(ResearchDocumentRow.kind == "pattern_contribution", ResearchDocumentRow.id == key))]
            counts = {name: sum(v[name] for v in values) for name in ("done", "resolved")}
        if counts["resolved"] >= MIN_SAMPLE_FOR_RATING:
            library.record_observation(business_archetype=archetype, channel_family=channel, query_pattern=trigger, close_rate=counts["done"] / counts["resolved"], sample_size=counts["resolved"])


if __name__ == "__main__":
    sys.exit(main())
