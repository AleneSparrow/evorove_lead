"""Research resource limits. Reserve before work; unknown failures retain reservations."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from pydantic import Field
from sqlalchemy import Column, String, BigInteger, CheckConstraint, select, update
from sqlalchemy.orm import Session
from evorove_lead.evidence import StrictModel, utcnow
from evorove_lead.sqlalchemy_models import Base

class BudgetExhausted(RuntimeError):
    pass

class BudgetLimits(StrictModel):
    queries: int = Field(default=10, ge=0)
    companies: int = Field(default=20, ge=0)
    pages: int = Field(default=60, ge=0)
    browser_calls: int = Field(default=2, ge=0)
    llm_calls: int = Field(default=24, ge=0)
    input_tokens: int = Field(default=60000, ge=0)
    output_tokens: int = Field(default=10000, ge=0)
    iterations: int = Field(default=3, ge=1, le=10)
    seconds: int = Field(default=900, ge=1)
    cost_microusd: int = Field(default=1000000, ge=0)
    daily_cost_microusd: int = Field(default=3000000, ge=0)

class BudgetDayRow(Base):
    __tablename__ = "research_budget_days"
    business_id = Column(String(128), primary_key=True)
    day = Column(String(10), primary_key=True)
    spent = Column(BigInteger, nullable=False, default=0)
    __table_args__ = (CheckConstraint("spent >= 0"),)

class DailyBudgetStore:
    def __init__(self, engine): self.engine = engine

    def reserve(self, business_id, day, amount, limit):
        with Session(self.engine) as session, session.begin():
            if self.engine.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            session.execute(insert(BudgetDayRow).values(business_id=business_id, day=day, spent=0).on_conflict_do_nothing())
            result = session.execute(update(BudgetDayRow).where(BudgetDayRow.business_id == business_id, BudgetDayRow.day == day, BudgetDayRow.spent + amount <= limit).values(spent=BudgetDayRow.spent + amount))
            if result.rowcount != 1:
                raise BudgetExhausted("tenant_daily_cost")

    def release(self, business_id, day, amount):
        with Session(self.engine) as session, session.begin():
            result = session.execute(update(BudgetDayRow).where(BudgetDayRow.business_id == business_id, BudgetDayRow.day == day, BudgetDayRow.spent >= amount).values(spent=BudgetDayRow.spent - amount))
            if result.rowcount != 1: raise ValueError("invalid budget release")

class ResearchBudget:
    def __init__(self, business_id, limits=None, *, daily_store=None, clock=utcnow, recorder=None):
        self.business_id = business_id
        self.limits = limits or BudgetLimits()
        self.daily_store, self.clock, self.recorder = daily_store, clock, recorder
        self.started_at = clock()
        self.used = {name: 0 for name in ("queries", "companies", "pages", "browser_calls", "llm_calls", "input_tokens", "output_tokens", "iterations", "cost_microusd")}
        self._lock = Lock()

    def check_time(self):
        from evorove_lead.run_ownership import check_ownership
        check_ownership()
        if self.clock() >= self.started_at + timedelta(seconds=self.limits.seconds):
            raise BudgetExhausted("deadline")

    def consume(self, **amounts):
        with self._lock:
            self.check_time()
            for name, amount in amounts.items():
                if name not in self.used or amount < 0: raise ValueError("invalid budget dimension")
                if self.used[name] + amount > getattr(self.limits, name): raise BudgetExhausted(name)
            day = self.clock().astimezone(timezone.utc).date().isoformat()
            if amounts.get("cost_microusd") and self.daily_store:
                self.daily_store.reserve(self.business_id, day, amounts["cost_microusd"], self.limits.daily_cost_microusd)
            for name, amount in amounts.items(): self.used[name] += amount
            if self.recorder: self.recorder(dict(self.used))
            return day

    def settle(self, reserved, actual, day):
        with self._lock:
            if any(actual[k] > reserved[k] for k in actual):
                raise BudgetExhausted("provider exceeded reservation")
            difference = reserved["cost_microusd"] - actual["cost_microusd"]
            if difference and self.daily_store:
                self.daily_store.release(self.business_id, day, difference)
            for key in actual: self.used[key] -= reserved[key] - actual[key]
            if self.recorder: self.recorder(dict(self.used))

class BudgetedSearch:
    """At most two attempts; each attempt reserves a query before I/O."""
    def __init__(self, client, budget, *, sleeper=None):
        import time
        self.client, self.budget = client, budget
        self.sleeper = sleeper or time.sleep
        self.errors, self.successes = [], 0

    def search(self, query):
        from evorove_lead.web_search import SearchError
        for attempt in range(2):
            self.budget.consume(queries=1)
            try:
                result = self.client.search(query)
            except SearchError as exc:
                # Never retry partial source failures or permanent responses.
                delay = exc.retry_after if exc.retry_after is not None else .1
                if exc.retryable and attempt == 0 and delay <= 2 and self.budget.used["queries"] < self.budget.limits.queries:
                    self.budget.check_time()
                    from datetime import timedelta
                    if self.budget.clock() + timedelta(seconds=delay) < self.budget.started_at + timedelta(seconds=self.budget.limits.seconds):
                        self.sleeper(delay)
                        continue
                self.errors.append(exc)
                if exc.hits:
                    self.successes += 1
                    return exc.hits
                raise
            self.budget.check_time()
            self.successes += 1
            return result
