"""Register every cycle-1 ORM table before creating or comparing metadata."""

from __future__ import annotations

from importlib import import_module

from evorove_lead.sqlalchemy_models import Base

_TABLE_MODULES = (
    "intelligence_store",
    "research_budget",
    "research_cache",
    "llm_usage",
    "research_delivery",
    "source_core_models",
)


def registered_metadata():
    for module in _TABLE_MODULES:
        import_module(f"evorove_lead.{module}")
    return Base.metadata
