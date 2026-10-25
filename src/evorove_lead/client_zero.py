"""Client 0 is Evorove selling itself.

Search metrics start here. This module does not message anyone and does not
print a found person's email, phone, or reason. Counts only.
"""

from __future__ import annotations

import os
from pathlib import Path

from evorove_lead.business import BusinessSeed
from evorove_lead.engine import LeadGenerationEngine
from evorove_lead.marketing_analysis import marketing_analyzer_from_env
from evorove_lead.materials import DepositedMaterial, load_deposited_materials
from evorove_lead.presence import HttpPresenceSource, PresenceSource
from evorove_lead.sqlalchemy_warehouse import flush_crm_deliveries_from_env
from evorove_lead.web_people_search import hypothesis_search_from_env

CLIENT_ZERO_SITE = "https://evorove.com"
CLIENT_ZERO_ARCHETYPE = "software subscription"

# `Path(__file__).resolve().parent.parent.parent` looked right in an editable
# checkout, but once this package is installed normally (the Docker image
# runs `pip install .`, not `-e .`), `__file__` resolves inside site-packages
# and three parents up lands nowhere near the repo -- this directory then
# silently existed and was always empty, and read_offer() correctly rejected
# the live-only fetch as ungrounded on every client-zero run. The repo is
# copied to and run from `/app` (see the Dockerfile's `WORKDIR`), so the
# process's own working directory is the reliable anchor, not the package's
# install location. `OWNER_MATERIALS_DIR` still lets a test or a different
# deployment layout override it explicitly.
OWNER_MATERIALS_DIR = Path(os.getenv("OWNER_MATERIALS_DIR") or (Path.cwd() / "owner-materials"))


class _WithOwnerMaterials:
    """The live site, plus whatever the owner deposited in `owner-materials/`.

    The live page is real, but its own marketing style (hero taglines,
    FAQ headings) reads poorly for `offer_reader.py`'s plain-English
    heuristics. This never replaces the live fetch -- it only adds
    deposited files ahead of it, so a clean, ordinary sentence there can
    stand as the offer's `what_we_sell` (used first) while the live page
    still supplies its own audience mentions and price. An empty or
    missing directory changes nothing: `owner-materials/` already exists
    as an optional, git-tracked spot for this (see its own README).
    """

    def __init__(self, live: PresenceSource, materials_dir: Path) -> None:
        self._live = live
        self._materials_dir = materials_dir

    def load(self, seed: BusinessSeed) -> tuple[DepositedMaterial, ...]:
        deposited: tuple[DepositedMaterial, ...] = ()
        if self._materials_dir.is_dir():
            deposited = load_deposited_materials(self._materials_dir)
        return deposited + self._live.load(seed)


def client_zero_presence() -> PresenceSource:
    return _WithOwnerMaterials(HttpPresenceSource(), OWNER_MATERIALS_DIR)


def client_zero_seed() -> BusinessSeed:
    """CRM tenant id is optional until the boards are glued. The site is not."""

    business_id = (os.getenv("EVOROVE_CLIENT_ZERO_BUSINESS_ID") or "").strip()
    return BusinessSeed(
        site_url=CLIENT_ZERO_SITE,
        business_id=business_id,
        business_archetype=CLIENT_ZERO_ARCHETYPE,
    )


def search_summary(result) -> dict[str, int | str]:
    """Counts the owner can read. Never a contact."""

    return {
        "status": result.status.value,
        "candidates": len(result.candidates),
        "handoffs": len(result.handoffs),
        "rejected": len(result.rejected),
        "messages_sent": 0,
    }


def run_client_zero_search(*, research_dry_run: bool = False) -> dict[str, int | str]:
    from evorove_lead.web_search import SearchError
    try:
        if research_dry_run and not (os.getenv("WEB_SEARCH_BASE_URL") or "").strip():
            raise SearchError("search_not_configured")
        search = hypothesis_search_from_env()
    except SearchError as exc:
        return {"status": "failed", "stop_reason": exc.code, "research_status": "failed", "research_stop_reason": exc.code,
                "candidates": 0, "handoffs": 0, "rejected": 0, "messages_sent": 0, "crm_pending": 0, "crm_redelivered": 0,
                "crm_accepted_cold": 0, "crm_blocked_reason": ""}
    if search is None:
        summary: dict[str, int | str] = {
            "status": "search_unconnected",
            "candidates": 0,
            "handoffs": 0,
            "rejected": 0,
            "messages_sent": 0,
        }
    else:
        research = None
        warehouse = None
        if research_dry_run:
            from evorove_lead.sqlalchemy_warehouse import warehouse_from_env
            from evorove_lead.research_config import research_pipeline_from_env
            from evorove_lead.market_intelligence import ResearchUnavailable
            warehouse = warehouse_from_env()
            research = research_pipeline_from_env(warehouse, search._client, dry_run=True)
            if research is None:
                raise ResearchUnavailable("research is not enabled")
        engine = LeadGenerationEngine(
            research_pipeline=research,
            warehouse=warehouse,
            presence=client_zero_presence(),
            hypothesis_search=search,
            marketing_analyzer=marketing_analyzer_from_env(),
            # Client 0 is a nationwide US SaaS product, not a local service
            # business with one city or service area -- there is no real
            # locality to infer here, only this product's own jargon
            # ("CRM", "Cold") that a text heuristic can mistake for one (real
            # bug found piloting this: "they land in CRM on Cold" read as a
            # place). The honest geo_radius for client 0 is the whole US
            # market, not a guess -- leave inference off (default `False`).
        )
        result = engine.generate(client_zero_seed())
        summary = search_summary(result)
        if result.stop_reason:
            summary["stop_reason"] = result.stop_reason
        if result.sent_messages:
            raise RuntimeError("cycle 1 must not send")
    if research_dry_run:
        summary["crm_redelivered"] = 0
        summary["crm_pending"] = 0
        summary["crm_accepted_cold"] = 0
        summary["crm_blocked_reason"] = ""
        if search is not None and research is not None:
            summary["research_run_id"] = research.last_run["id"]
            summary["research_status"] = research.last_run["status"]
            summary["research_stop_reason"] = research.last_run.get("stop_reason", "")
        return summary
    delivery = flush_crm_deliveries_from_env()
    summary["crm_redelivered"] = delivery["redelivered"]
    summary["crm_pending"] = delivery["pending"]
    summary["crm_accepted_cold"] = delivery.get("accepted_cold", 0)
    summary["crm_blocked_reason"] = delivery.get("blocked_reason", "")
    return summary


def preflight(*, probe_search=False):
    """Read only nonsecret configuration flags; optionally probe configured SearxNG."""
    from evorove_lead.web_search import client_from_env
    report = {
        "search_configured": bool(os.getenv("WEB_SEARCH_BASE_URL")),
        "research_enabled": os.getenv("RESEARCH_ENABLED") == "1",
        "paid_calls_authorized": os.getenv("RESEARCH_ALLOW_PAID_LLM") == "1",
        "delivery_enabled": os.getenv("RESEARCH_CRM_DELIVERY") == "1",
        "cycle2_isolated": os.getenv("CRM_CYCLE2_ISOLATED") == "1",
        "crm_configured": bool((os.getenv("CRM_BASE_URL") or "").strip() and (os.getenv("INTERNAL_TASK_SECRET") or "")),
        "search_probe": "not_run", "blockers": [],
    }
    if not report["search_configured"]: report["blockers"].append("WEB_SEARCH_BASE_URL is not configured")
    if not report["research_enabled"]: report["blockers"].append("research is not enabled")
    if not report["paid_calls_authorized"]: report["blockers"].append("paid LLM calls require separate authorization")
    if report["delivery_enabled"] and not report["cycle2_isolated"]:
        report["blockers"].append("CRM delivery requires CRM_CYCLE2_ISOLATED=1 on a CRM without cycle 2 handlers")
    if report["crm_configured"] and not report["cycle2_isolated"]:
        report["blockers"].append("configured CRM is not isolated from cycle 2")
    if probe_search and report["search_configured"]:
        from evorove_lead.web_search import SearchError
        try:
            client = client_from_env()
            hits = client.search("Evorove public website") if client else ()
        except SearchError as exc:
            report["blockers"].append(exc.code)
            hits = ()
        report["search_probe"] = "results_received" if hits else "empty_or_unavailable"
        if not hits: report["blockers"].append("search probe did not establish working retrieval")
    return report


def client_zero_source_analysis(business_id: str):
    """Brief already on disk. The buyer is a US B2B business with a high-value deal."""
    from evorove_lead.source_planner import client_zero_analysis
    path = OWNER_MATERIALS_DIR / "service.txt"
    brief = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "US B2B businesses" not in brief:
        raise RuntimeError("client 0 brief is missing the confirmed ICP")
    return client_zero_analysis(business_id, brief)


def resolve_source_plan(engine, analysis, playbook):
    """A playbook replaces the model planner. Without one, use the cache or the catalog rules."""
    from evorove_lead.lead_playbooks import plan_from_playbook
    from evorove_lead.source_planner import deterministic_source_plan, load_cached_plan
    if playbook is not None:
        return plan_from_playbook(analysis, playbook)
    return load_cached_plan(engine, analysis.business_id, analysis.brief_hash()) or deterministic_source_plan(analysis)


def run_source_dry_run(*, collect_only=False, analyze_only=False, plan_only=False, niche="") -> dict:
    """Separate collect-once mode. Explicit source DB, no CRM sink or old research.

    `--niche` selects a lead playbook and does not call the model. Without it,
    collection follows the cached model plan for this brief hash, or the
    deterministic catalog plan when that cache is empty.
    """
    from sqlalchemy import create_engine
    from evorove_lead.lead_playbooks import playbook_by_id
    from evorove_lead.source_adapters import PublicSourceHTTP
    from evorove_lead.source_collection import SourceCollector
    from evorove_lead.source_analysis import SourceAnalyzer
    from evorove_lead.source_planner import adapters_for_plan, plan_with_model
    playbook = playbook_by_id(niche) if niche else None
    db_url = os.getenv('SOURCE_DATABASE_URL')
    if not db_url:
        raise RuntimeError('SOURCE_DATABASE_URL is required for source mode')
    business_id = os.getenv('EVOROVE_CLIENT_ZERO_BUSINESS_ID') or 'client-zero'
    engine = create_engine(db_url, future=True)
    analysis = client_zero_source_analysis(business_id)
    summary = {'mode':'source_dry_run','business_id':business_id,'niche':playbook.id if playbook else '',
        'crm_writes':0,'cold':0,'messages_sent':0}
    if plan_only:
        if playbook is not None:
            plan = resolve_source_plan(engine, analysis, playbook)
        elif os.getenv('SOURCE_ALLOW_PAID_LLM') == '1':
            model = os.getenv('ANTHROPIC_MODEL')
            if model != 'claude-sonnet-5-5':
                raise RuntimeError('source mode requires explicitly priced claude-sonnet-5-5')
            key = os.getenv('ANTHROPIC_API_KEY')
            if not key:
                raise RuntimeError('ANTHROPIC_API_KEY is required for paid analysis')
            from evorove_lead.research_config import AnthropicResearchCompletion
            plan = plan_with_model(engine, analysis, AnthropicResearchCompletion(key, model, effort='medium'))
        else:
            plan = resolve_source_plan(engine, analysis, None)
        summary['plan'] = plan.as_dict()
        engine.dispose()
        return summary
    if not analyze_only:
        plan = resolve_source_plan(engine, analysis, playbook)
        summary['plan'] = {'model_used': plan.model_used, 'brief_hash': plan.brief_hash,
            'niche': playbook.id if playbook else '',
            'selected': [item.source_id for item in plan.selected],
            'queries': [query for item in plan.selected for query in item.queries]}
        http = PublicSourceHTTP(max_requests=int(os.getenv('SOURCE_MAX_REQUESTS','40')))
        search = None
        if any(item.source_id == 'searxng_web' for item in plan.selected):
            from evorove_lead.web_search import client_from_env
            search = client_from_env()
        collector = SourceCollector(engine, http, ttl_days=int(os.getenv('SOURCE_SITE_TTL_DAYS','7')))
        summary['collected'] = collector.collect(business_id, adapters_for_plan(plan, http, search))
        summary['http_requests'] = http.count
    if not collect_only:
        classifier = None
        if playbook is None and os.getenv('SOURCE_ALLOW_PAID_LLM') == '1':
            model = os.getenv('ANTHROPIC_MODEL')
            if model != 'claude-sonnet-5-5':
                raise RuntimeError('source mode requires explicitly priced claude-sonnet-5-5')
            key = os.getenv('ANTHROPIC_API_KEY')
            if not key:
                raise RuntimeError('ANTHROPIC_API_KEY is required for paid analysis')
            from evorove_lead.research_config import AnthropicResearchCompletion
            classifier = AnthropicResearchCompletion(key, model, effort='medium')
        report, _cold_ready = SourceAnalyzer(engine, classifier, playbook=playbook).run(business_id)
        summary['analysis'] = report
    engine.dispose()
    return summary

def main() -> None:
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--probe-search", action="store_true")
    parser.add_argument("--research-dry-run", action="store_true")
    parser.add_argument("--source-dry-run", action="store_true")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--plan-sources", action="store_true")
    parser.add_argument("--list-niches", action="store_true")
    parser.add_argument("--niche", default="")
    args = parser.parse_args()
    if args.list_niches:
        if args.source_dry_run or args.preflight or args.research_dry_run or args.probe_search or args.niche:
            parser.error("--list-niches only lists playbooks")
        from evorove_lead.lead_playbooks import niche_menu
        print(json.dumps(niche_menu(), indent=2))
        return
    if args.niche and not args.source_dry_run:
        parser.error("--niche requires --source-dry-run")
    if args.collect_only and args.analyze_only:
        parser.error("choose only one of --collect-only and --analyze-only")
    if args.plan_sources and (args.collect_only or args.analyze_only):
        parser.error("--plan-sources does not collect or analyze")
    if (args.collect_only or args.analyze_only or args.plan_sources) and not args.source_dry_run:
        parser.error("--collect-only, --analyze-only, and --plan-sources require --source-dry-run")
    if args.source_dry_run:
        print(json.dumps(run_source_dry_run(collect_only=args.collect_only, analyze_only=args.analyze_only, plan_only=args.plan_sources, niche=args.niche), indent=2))
        return
    if args.preflight:
        report = preflight(probe_search=args.probe_search)
        print(json.dumps(report, indent=2))
        raise SystemExit(2 if report["blockers"] else 0)
    summary = run_client_zero_search(research_dry_run=args.research_dry_run)
    if args.research_dry_run:
        print(json.dumps(summary, indent=2))
        raise SystemExit(0 if summary.get("research_status") == "completed" else 2)
    print(
        "client_zero status={status} candidates={candidates} "
        "handoffs={handoffs} rejected={rejected} messages_sent={messages_sent} "
        "crm_pending={crm_pending} crm_redelivered={crm_redelivered}".format(
            **summary
        )
    )
    if summary.get("stop_reason"):
        print("stop_reason=" + str(summary["stop_reason"]))
    if summary["status"] in {"search_unconnected", "failed", "partial", "budget_exhausted"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
