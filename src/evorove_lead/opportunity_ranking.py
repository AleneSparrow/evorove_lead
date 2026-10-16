"""Prioritization of already-qualified opportunities, never an admission rule."""
from evorove_lead.evidence import StrictModel
from evorove_lead.qualification import Decision

class OpportunityRank(StrictModel):
    score: float
    explanation: tuple[str, ...]
    unknown_dimensions: tuple[str, ...]

class CommercialOpportunityRanker:
    def rank(self, gate, assessment):
        if gate.decision != Decision.QUALIFIED:
            raise ValueError("only qualified opportunities can be ranked")
        weights = {"product_fit": .25, "need_evidence": .25, "timing": .15,
                   "commercial_opportunity": .10, "contact_relevance": .10, "evidence_reliability": .15}
        score, explanations, unknown = 0., [], []
        for name, weight in weights.items():
            dim = getattr(assessment, name)
            if dim.score is None:
                unknown.append(name)
            else:
                score += weight * dim.score
            explanations.append(f"{name}: {dim.explanation}")
        return OpportunityRank(score=round(score, 4), explanation=tuple(explanations), unknown_dimensions=tuple(unknown))
