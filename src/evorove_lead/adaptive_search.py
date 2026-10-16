"""Interpretable conservative allocation; retain exploration, ignore unresolved sales."""
from dataclasses import dataclass
from math import sqrt

@dataclass(frozen=True)
class StrategyObservation:
    key: str
    discovered: int = 0
    supported: int = 0
    qualified: int = 0
    reliable_sources: int = 0
    sources: int = 0
    cost_microusd: int = 0
    mature_closed: int = 0
    mature_resolved: int = 0
    independent_cohorts: int = 0

class AdaptiveSearchPlanner:
    def allocate(self, observations, total_queries, *, min_sample=30, min_cohorts=3):
        if total_queries < 0: raise ValueError("negative query budget")
        observations = sorted(observations, key=lambda o: o.key)
        allocation = {o.key: 0 for o in observations}
        if not observations: return allocation
        # Every strategy receives one exploration query where budget permits.
        for o in observations[:total_queries]: allocation[o.key] = 1
        remaining = total_queries - sum(allocation.values())
        def priority(o):
            if o.discovered < min_sample or o.independent_cohorts < min_cohorts: return .5
            quality = (min(o.qualified, o.discovered) + 1) / (o.discovered + 2)
            reliability = (min(o.reliable_sources, o.sources) + 1) / (o.sources + 2)
            sales = ((o.mature_closed + 1)/(o.mature_resolved + 2)) if o.mature_resolved >= min_sample else .5
            cost = o.cost_microusd / max(o.qualified, 1) / 1000000
            return quality * reliability * (.5 + sales) / (1 + cost)
        for _ in range(remaining):
            best = max(observations, key=lambda o: (priority(o)/(allocation[o.key]+1), o.key))
            allocation[best.key] += 1
        return allocation
