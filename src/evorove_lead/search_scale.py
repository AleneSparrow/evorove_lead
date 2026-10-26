"""Query shape for a later search. This module does not run a search."""
from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "search_scale.json"


def load_search_scale(path: Path | None = None) -> dict:
    data = json.loads((path or _PATH).read_text(encoding="utf-8"))
    if data.get("describes") != "search_scale":
        raise RuntimeError("search scale file must describe search_scale")
    if int(data["google_cse_free_queries_per_day"]) != 100:
        raise RuntimeError("Google Programmable Search free budget is 100 queries a day")
    if int(data["searxng_queries_per_day"]) < 1:
        raise RuntimeError("SearxNG daily budget")
    return data


def query_catalog(playbook, scale: dict | None = None) -> tuple[str, ...]:
    """One line per active subsegment term and city. Needs-research terms stay out."""
    data = scale or load_search_scale()
    cities = tuple(data["cities"])
    found = []
    for sub in playbook.subsegments:
        if sub.get("status") == "needs_research":
            continue
        for term in sub["terms"]:
            for city in cities:
                found.append(f"{term} {city}")
    return tuple(found)


def next_batch(state: dict, queries: tuple[str, ...], *, day: str, budget: int) -> tuple[tuple[str, ...], dict]:
    """The day's slice. The same day returns nothing and does not move the cursor."""
    if state.get("day") == day:
        return (), dict(state)
    start = int(state.get("next_index") or 0) % len(queries) if queries else 0
    if not queries or budget < 1:
        return (), {"day": day, "next_index": start, "last_count": 0}
    batch = []
    index = start
    for _ in range(min(budget, len(queries))):
        batch.append(queries[index])
        index = (index + 1) % len(queries)
    return tuple(batch), {"day": day, "next_index": index, "last_count": len(batch)}


def expected_inflow(*, queries_per_day: int, domains_per_query: float, a_rate: float, days: int = 30) -> dict:
    """Arithmetic only. The caller supplies the observed rate. This does not search."""
    domains = queries_per_day * domains_per_query * days
    return {
        "domains": domains,
        "A": domains * a_rate,
        "days": days,
    }
