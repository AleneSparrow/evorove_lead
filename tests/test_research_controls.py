from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from evorove_lead.sqlalchemy_models import Base
from evorove_lead.research_budget import *
from evorove_lead.research_cache import *
from evorove_lead.llm_usage import *
from evorove_lead.research_reflection import *
from evorove_lead.adaptive_search import *

def test_tenant_daily_budget_survives_runs_and_cache_is_scoped():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    daily = DailyBudgetStore(engine)
    limits = BudgetLimits(cost_microusd=10, daily_cost_microusd=15)
    ResearchBudget("a", limits, daily_store=daily).consume(cost_microusd=10)
    with pytest.raises(BudgetExhausted): ResearchBudget("a", limits, daily_store=daily).consume(cost_microusd=10)
    ResearchBudget("b", limits, daily_store=daily).consume(cost_microusd=10)
    cache = ResearchCache(engine)
    cache.put("a", "key", {"value": 1})
    assert cache.get("b", "key") is None
    assert cache.get("a", "key") == {"value": 1}
    cache.put("a", "key", {}, ttl=timedelta(seconds=-1))
    assert cache.get("a", "key") is None

def test_no_call_when_budget_exhausted_and_unknown_failure_charged():
    class Provider:
        calls = 0
        def complete_bounded(self, **kwargs):
            self.calls += 1
            raise RuntimeError("provider down")
    class Ledger:
        def record(self, *args): pass
    provider = Provider()
    b = ResearchBudget("a", BudgetLimits(llm_calls=1))
    c = BudgetedCompletion(provider, b, Ledger(), "run")
    with pytest.raises(RuntimeError): c.complete(system="s", user="u")
    assert b.used["cost_microusd"] > 0
    with pytest.raises(BudgetExhausted): c.complete(system="s", user="u")
    assert provider.calls == 1

def test_reflection_terminates_and_records_changes():
    class Reasoner:
        calls = 0
        def ask(self, *args):
            self.calls += 1
            return Reflection(cause="weak signal", evidence=("obs",), justified=True, revised_queries=(f"query {self.calls}",), explanation="try explicit request")
    reasoner = Reasoner()
    records = []
    b = ResearchBudget("a")
    result = ResearchReflectionLoop(reasoner, b, records.append).run(("initial",), lambda q: {"qualified": 0, "observation_id": "obs"}, context={})
    assert len(result) == 3 and reasoner.calls == 2
    assert sum("strategy_change" in r for r in records) == 2

def test_adaptive_small_samples_do_not_drive_budget_and_exploration_remains():
    planner = AdaptiveSearchPlanner()
    tiny = [StrategyObservation("a", discovered=2, qualified=2), StrategyObservation("b", discovered=2)]
    allocation = planner.allocate(tiny, 10)
    assert allocation == {"a": 5, "b": 5}
    mature = [StrategyObservation("a", discovered=100, qualified=70, sources=100, reliable_sources=99, independent_cohorts=4), StrategyObservation("b", discovered=100, independent_cohorts=4)]
    allocation = planner.allocate(mature, 10)
    assert allocation["a"] > allocation["b"] >= 1

def test_deadline_and_iteration_budget():
    now = datetime.now(timezone.utc)
    b = ResearchBudget("a", clock=lambda: now)
    now += timedelta(seconds=901)
    with pytest.raises(BudgetExhausted, match="deadline"): b.consume(queries=1)


def test_hypothesis_iterations_are_bounded_and_queries_remain_run_wide():
    budget = ResearchBudget("a", BudgetLimits(iterations=1, queries=1))
    budget.consume_iteration("first")
    assert not budget.iterations_remaining("first")
    with pytest.raises(BudgetExhausted, match="iterations"):
        budget.consume_iteration("first")
    budget.consume_iteration("second")
    assert budget.used["iterations"] == 2
    budget.consume(queries=1)
    with pytest.raises(BudgetExhausted, match="queries"):
        budget.consume(queries=1)
