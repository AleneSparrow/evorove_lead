"""Bounded strategy revision. No revision may change qualification policy."""
from pydantic import Field
from evorove_lead.evidence import StrictModel
from evorove_lead.research_budget import BudgetExhausted

class Reflection(StrictModel):
    cause: str
    evidence: tuple[str, ...]
    justified: bool
    revised_queries: tuple[str, ...] = Field(default=(), max_length=2)
    explanation: str

class ResearchReflectionLoop:
    def __init__(self, reasoner, budget, record):
        self.reasoner, self.budget, self.record = reasoner, budget, record

    def run(self, initial_queries, execute, *, context, iteration_scope=None):
        queries = tuple(initial_queries)
        seen = set()
        history = []
        for iteration in range(self.budget.limits.iterations):
            if iteration_scope is None:
                self.budget.consume(iterations=1)
            else:
                self.budget.consume_iteration(iteration_scope)
            queries = tuple(q for q in queries if q not in seen)
            if not queries: break
            seen.update(queries)
            result = execute(queries)
            history.append(result)
            self.record({"iteration": iteration + 1, "queries": queries, "result": result})
            if result.get("infrastructure_failure") or result.get("qualified", 0) or iteration + 1 == self.budget.limits.iterations: break
            reflection = self.reasoner.ask("Analyze failed research: segment, signal, query, sources, geography, assumptions or commercial value. Recommend additional research only if justified. Never relax evidence requirements. Evidence references are observation IDs from history.", Reflection, {"context": context, "history": history})
            ids = {str(item["observation_id"]) for item in history if "observation_id" in item}
            if not set(reflection.evidence) <= ids:
                raise ValueError("reflection references unknown observations")
            self.record({"iteration": iteration + 1, "strategy_change": reflection.model_dump(mode="json")})
            if not reflection.justified or not reflection.revised_queries: break
            queries = reflection.revised_queries
        return history
