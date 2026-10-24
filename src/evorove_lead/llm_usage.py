"""Budgeted completion adapter. Rates are explicit, versioned configuration."""
from __future__ import annotations
from dataclasses import dataclass
from math import ceil
from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import Session
from evorove_lead.evidence import utcnow
from evorove_lead.sqlalchemy_models import Base, JSON_VALUE
from evorove_lead.warehouse import new_id

@dataclass(frozen=True)
class CompletionReply:
    text: str
    input_tokens: int
    output_tokens: int

class UsageRow(Base):
    __tablename__ = "research_usage"
    business_id = Column(String(128), primary_key=True)
    id = Column(String(128), primary_key=True)
    run_id = Column(String(128), nullable=False)
    payload = Column(JSON_VALUE, nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False)

class LLMUsageLedger:
    def __init__(self, engine): self.engine = engine
    def record(self, business_id, run_id, payload):
        with Session(self.engine) as session, session.begin():
            session.add(UsageRow(business_id=business_id, id=new_id("usage"), run_id=run_id, payload=payload, recorded_at=utcnow()))

class BudgetedCompletion:
    def __init__(self, completion, budget, ledger, run_id, *, max_output_tokens=4000,
                 input_usd_per_million=3., output_usd_per_million=15., model="configured", pricing_version="2026-10-09",
                 effort="low"):
        self.completion, self.budget, self.ledger, self.run_id = completion, budget, ledger, run_id
        self.max_output_tokens = max_output_tokens
        self.input_rate, self.output_rate = input_usd_per_million, output_usd_per_million
        self.model, self.pricing_version, self.effort = model, pricing_version, effort
        if min(self.input_rate, self.output_rate) < 0: raise ValueError("negative rate")

    def complete(self, *, system, user):
        # UTF-8 bytes plus framing is a conservative token upper bound for this
        # text-only endpoint. A provider must honor max_tokens on the request.
        input_bound = len(system.encode()) + len(user.encode()) + 1024
        reserved = dict(input_tokens=input_bound, output_tokens=self.max_output_tokens,
                        cost_microusd=ceil(input_bound*self.input_rate + self.max_output_tokens*self.output_rate))
        day = self.budget.consume(llm_calls=1, **reserved)
        event = {"model": self.model, "pricing_version": self.pricing_version, "reservation": reserved, "status": "reserved"}
        self.ledger.record(self.budget.business_id, self.run_id, event)
        try:
            from evorove_lead.run_ownership import guarded_call
            reply = guarded_call(self.completion.complete_bounded, system=system, user=user, max_tokens=self.max_output_tokens, effort=self.effort)
            actual = dict(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                          cost_microusd=ceil(reply.input_tokens*self.input_rate + reply.output_tokens*self.output_rate))
            if min(reply.input_tokens, reply.output_tokens) < 0: raise ValueError("invalid provider usage")
            self.budget.settle(reserved, actual, day)
            self.ledger.record(self.budget.business_id, self.run_id, {**event, "status": "completed", "usage": actual, "invoice_cost": None})
            self.budget.check_time()
            return reply.text
        except Exception as exc:
            from evorove_lead.market_intelligence import TruncatedStructuredCompletion
            if isinstance(exc, TruncatedStructuredCompletion):
                self.ledger.record(self.budget.business_id, self.run_id, {**event, "status": "truncated", **exc.metrics})
            else:
                self.ledger.record(self.budget.business_id, self.run_id, {**event, "status": "failed_or_unknown"})
            raise
