"""Pick catalog sources from a client analysis. The model may not invent sources."""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from evorove_lead.evidence import digest
from evorove_lead.search_planner import buyer_query_set
from evorove_lead.source_catalog import CATALOG, CATALOG_VERSION, enabled_sources, source_by_id
from evorove_lead.source_core_models import SourcePlanRow

PLAN_MAX_TOKENS = 800
PLAN_EFFORT = "medium"


class PlanRejected(ValueError):
    """The model returned a source that is unknown or disabled, or an empty plan."""


@dataclass(frozen=True)
class ClientAnalysis:
    business_id: str
    brief: str
    customer_categories: tuple[str, ...] = ()
    geography: str = ""
    localities: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    icp_segments: tuple[str, ...] = ()
    icp_signals: tuple[str, ...] = ()
    search_strategy: tuple[str, ...] = ()
    buyer_role: str = ""
    disqualifiers: tuple[str, ...] = ()

    def blob(self) -> str:
        parts = [self.brief, self.geography, self.buyer_role, *self.customer_categories, *self.localities,
            *self.services, *self.icp_segments, *self.icp_signals, *self.search_strategy, *self.disqualifiers]
        return "\n".join(part for part in parts if part).casefold()

    def as_dict(self) -> dict:
        return {"business_id": self.business_id, "brief": self.brief,
            "customer_categories": list(self.customer_categories), "geography": self.geography,
            "localities": list(self.localities), "services": list(self.services),
            "icp_segments": list(self.icp_segments), "icp_signals": list(self.icp_signals),
            "search_strategy": list(self.search_strategy), "buyer_role": self.buyer_role,
            "disqualifiers": list(self.disqualifiers)}

    def brief_hash(self) -> str:
        body = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)
        return digest(CATALOG_VERSION + "\n" + body)


@dataclass(frozen=True)
class PlannedSource:
    source_id: str
    why: str
    queries: tuple[str, ...]
    filters: dict


@dataclass(frozen=True)
class SourcePlan:
    brief_hash: str
    selected: tuple[PlannedSource, ...]
    excluded: tuple[dict, ...]
    model_used: bool

    def as_dict(self) -> dict:
        return {"brief_hash": self.brief_hash, "model_used": self.model_used,
            "selected": [{"id": item.source_id, "why": item.why, "queries": list(item.queries), "filters": item.filters}
                for item in self.selected],
            "excluded": [dict(item) for item in self.excluded]}


def client_zero_analysis(business_id: str, brief: str) -> ClientAnalysis:
    """The confirmed client-0 brief: small SaaS and online businesses in the US."""
    return ClientAnalysis(business_id=business_id, brief=brief.strip(),
        customer_categories=("small SaaS and online businesses",), geography="United States",
        services=("lead generation and sales automation",),
        icp_segments=("small SaaS and online businesses in the US",),
        icp_signals=("public site of a small US SaaS or online business",),
        buyer_role="founder or owner")


def dentist_example() -> ClientAnalysis:
    return ClientAnalysis(business_id="example-dentist",
        brief="A local dental practice in Austin, Texas. The owner is the dentist. Patients book visits at the office.",
        customer_categories=("local dental practice",), geography="Austin, Texas, United States",
        localities=("Austin",), services=("dental care",), icp_segments=("local dental practice in Austin",),
        icp_signals=("patients book visits",), buyer_role="owner")


def pet_store_example() -> ClientAnalysis:
    return ClientAnalysis(business_id="example-pet-store",
        brief="An online store selling pet supplies to customers across the United States. There is no single shop.",
        customer_categories=("online store for pet supplies",), geography="United States",
        services=("pet supplies",), icp_segments=("online pet supply store in the US",),
        icp_signals=("shoppers comparing pet supplies",), buyer_role="owner")


def _matches(spec, analysis: ClientAnalysis) -> bool:
    if spec.universal:
        return True
    blob = analysis.blob()
    if any(tag.casefold() in blob for tag in spec.niche_tags):
        return True
    return bool(spec.requires_locality and analysis.localities)


def queries_for(analysis: ClientAnalysis, source_id: str) -> tuple[str, ...]:
    if source_id != "searxng_web":
        return ()
    segment = analysis.customer_categories[0] if analysis.customer_categories else (analysis.icp_segments[0] if analysis.icp_segments else "")
    problem = analysis.icp_signals[0] if analysis.icp_signals else (analysis.services[0] if analysis.services else analysis.brief)
    place = analysis.localities[0] if analysis.localities else (analysis.geography or "United States")
    planned = buyer_query_set(problem, segment, place, signals=analysis.icp_signals, strategies=analysis.search_strategy)
    if planned:
        cleaned = []
        for query in planned:
            kept = []
            for token in query.split():
                if token.casefold() not in {item.casefold() for item in kept}:
                    kept.append(token)
            cleaned.append(" ".join(kept))
        return tuple(dict.fromkeys(cleaned))
    fallback = " ".join(part for part in (segment, place) if part).strip()[:120]
    return (fallback,) if len(fallback) >= 8 else ()


def filters_for(analysis: ClientAnalysis, source_id: str) -> dict:
    if source_id == "searxng_web":
        place = analysis.localities[0] if analysis.localities else analysis.geography
        return {"geography": place} if place else {}
    if source_id == "sec_form_d":
        return {"form": "D", "country": "US"}
    if source_id == "osm_overpass" and analysis.localities:
        return {"locality": analysis.localities[0]}
    return {}


def _exclusion(spec, analysis: ClientAnalysis) -> dict:
    matched = _matches(spec, analysis) and not spec.universal
    if spec.status != "enabled":
        why = spec.disabled_reason
        if matched:
            why = spec.disabled_reason + " The client analysis matches this source, so it would have been eligible."
        return {"id": spec.id, "why": why}
    tags = ", ".join(spec.niche_tags) or "its described population"
    return {"id": spec.id, "why": f"Not selected: this client's analysis does not mention {tags}. {spec.why_leads}"}


def deterministic_source_plan(analysis: ClientAnalysis) -> SourcePlan:
    """Rank enabled sources whose described population appears in the analysis.

    This is the plan used when the model is not called. A later model call may
    reorder enabled sources; it still cannot add an id from outside the catalog.
    """
    ranked = []
    for spec in CATALOG:
        if spec.status == "enabled" and _matches(spec, analysis):
            ranked.append(spec)
    ranked.sort(key=lambda spec: (spec.universal, -sum(tag.casefold() in analysis.blob() for tag in spec.niche_tags), spec.id))
    selected_ids = {spec.id for spec in ranked}
    selected = tuple(PlannedSource(spec.id, spec.why_leads, queries_for(analysis, spec.id), filters_for(analysis, spec.id)) for spec in ranked)
    excluded = tuple(_exclusion(spec, analysis) for spec in CATALOG if spec.id not in selected_ids)
    return SourcePlan(analysis.brief_hash(), selected, excluded, model_used=False)


def _enabled_prompt() -> str:
    rows = [{"id": spec.id, "description": spec.description, "niche_tags": list(spec.niche_tags),
        "provides": list(spec.provides), "why_leads": spec.why_leads, "universal": spec.universal}
        for spec in enabled_sources()]
    return json.dumps(rows, ensure_ascii=False)


def plan_system_prompt() -> str:
    return ("You choose public lead sources for one business. Return JSON only with keys selected and excluded. "
        "Each selected item has id, why, queries, and filters. Each excluded item has id and why. "
        "selected ids must be copied from the enabled catalog in the user message. Do not invent sources. "
        "Choose a source only when its described population is where this client's buyers appear in public. "
        "Do not choose a source because its API is convenient. "
        "why is one or two sentences. queries has at most 4 strings, each at most 120 characters, using the client's own words. "
        "Do not invent a price, discount, guarantee, or legal claim.")


def plan_user_prompt(analysis: ClientAnalysis) -> str:
    return json.dumps({"client": analysis.as_dict(), "enabled_sources": json.loads(_enabled_prompt())}, ensure_ascii=False)


def validate_model_plan(analysis: ClientAnalysis, payload: dict) -> SourcePlan:
    """Accept only known enabled ids. Disabled and unknown ids reject the whole plan."""
    if not isinstance(payload, dict):
        raise PlanRejected("plan is not an object")
    raw = payload.get("selected")
    if not isinstance(raw, list) or not raw:
        raise PlanRejected("empty")
    selected = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise PlanRejected("selected item")
        source_id = item.get("id")
        spec = source_by_id(source_id) if isinstance(source_id, str) else None
        if spec is None:
            raise PlanRejected(f"unknown:{source_id}")
        if spec.status != "enabled":
            raise PlanRejected(f"disabled:{source_id}")
        if source_id in seen:
            raise PlanRejected(f"duplicate:{source_id}")
        seen.add(source_id)
        why = item.get("why")
        if not isinstance(why, str) or not why.strip() or len(why) > 500:
            raise PlanRejected("why")
        queries = item.get("queries", [])
        if queries is None:
            queries = []
        if not isinstance(queries, list) or len(queries) > 4 or any(not isinstance(q, str) or len(q) > 120 or not q.strip() for q in queries):
            raise PlanRejected("queries")
        if spec.universal and not queries:
            raise PlanRejected("queries")
        filters = item.get("filters") or {}
        if not isinstance(filters, dict) or any(not isinstance(key, str) for key in filters):
            raise PlanRejected("filters")
        selected.append(PlannedSource(source_id, why.strip(), tuple(q.strip() for q in queries), filters))
    excluded_why = {}
    raw_excluded = payload.get("excluded", [])
    if not isinstance(raw_excluded, list):
        raise PlanRejected("excluded")
    for item in raw_excluded:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise PlanRejected("excluded item")
        if source_by_id(item["id"]) is None:
            raise PlanRejected(f"unknown:{item['id']}")
        if isinstance(item.get("why"), str) and item["why"].strip():
            excluded_why[item["id"]] = item["why"].strip()
    excluded = []
    for spec in CATALOG:
        if spec.id in seen:
            continue
        if spec.id in excluded_why:
            excluded.append({"id": spec.id, "why": excluded_why[spec.id]})
        else:
            excluded.append(_exclusion(spec, analysis))
    return SourcePlan(analysis.brief_hash(), tuple(selected), tuple(excluded), model_used=True)


def load_cached_plan(engine, business_id: str, brief_hash: str) -> SourcePlan | None:
    with Session(engine) as db:
        row = db.get(SourcePlanRow, (business_id, brief_hash))
        if row is None:
            return None
        payload = json.loads(row.plan_json)
    return SourcePlan(payload["brief_hash"],
        tuple(PlannedSource(item["id"], item["why"], tuple(item["queries"]), dict(item["filters"])) for item in payload["selected"]),
        tuple(payload["excluded"]), bool(payload["model_used"]))


def cache_plan(engine, business_id: str, plan: SourcePlan, model: str, *, clock=None) -> None:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    with Session(engine) as db, db.begin():
        db.merge(SourcePlanRow(business_id=business_id, brief_hash=plan.brief_hash, plan_json=json.dumps(plan.as_dict()),
            model=model, created_at=now))


def adapters_for_plan(plan: SourcePlan, http, search=None):
    """Build only the adapters the plan selected. Disabled catalog ids are not constructed."""
    from evorove_lead.source_adapters import HNAlgoliaAdapter, SecFormDAdapter
    from evorove_lead.source_fallback import UniversalWebFallback
    adapters = []
    for item in plan.selected:
        spec = source_by_id(item.source_id)
        if spec is None or spec.status != "enabled":
            continue
        if spec.id in {"hn_show", "hn_ask", "hn_hiring"}:
            adapters.append(HNAlgoliaAdapter(http, feed=spec.id.split("_", 1)[1]))
        elif spec.id == "sec_form_d":
            adapters.append(SecFormDAdapter(http))
        elif spec.id == "searxng_web":
            adapters.append(UniversalWebFallback(http, search, item.queries))
    return tuple(adapters)


def plan_with_model(engine, analysis: ClientAnalysis, completion, *, model="claude-sonnet-5-5", clock=None) -> SourcePlan:
    """One bounded call per analysis hash. A cached plan is not sent to the model again."""
    cached = load_cached_plan(engine, analysis.business_id, analysis.brief_hash())
    if cached is not None and cached.model_used:
        return cached
    reply = completion.complete_bounded(system=plan_system_prompt(), user=plan_user_prompt(analysis),
        max_tokens=PLAN_MAX_TOKENS, effort=PLAN_EFFORT)
    try:
        payload = json.loads(reply.text)
    except ValueError as exc:
        raise PlanRejected("invalid json") from exc
    plan = validate_model_plan(analysis, payload)
    cache_plan(engine, analysis.business_id, plan, model, clock=clock)
    return plan
