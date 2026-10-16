"""Read-only, tenant/run-scoped export for manual pilot review. No network calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from evorove_lead.intelligence_store import IntelligenceStore, ResearchDocumentRow
from evorove_lead.sqlalchemy_models import HypothesisRow, TraceRow, RejectedTraceRow


def build_report(engine, business_id: str, run_id: str) -> dict:
    store = IntelligenceStore(engine)
    run = store.get(business_id, "run", run_id)
    if run is None:
        raise ValueError("research run not found for this tenant")
    attribution = [a for a in store.list(business_id, "hypothesis_attribution") if a["run_id"] == run_id]
    hypothesis_ids = [a["hypothesis_id"] for a in attribution]
    with Session(engine) as session:
        hypotheses = {h.id: h for h in session.scalars(select(HypothesisRow).where(
            HypothesisRow.business_id == business_id, HypothesisRow.id.in_(hypothesis_ids)))}
        rows = session.scalars(select(ResearchDocumentRow).where(
            ResearchDocumentRow.business_id == business_id, ResearchDocumentRow.kind == "qualification"))
        candidates = []
        for row in rows:
            q = row.payload
            if q.get("run_id") != run_id:
                continue
            evidence = q["evidence"]
            name_claims = [c for c in evidence["claims"] if c["predicate"] in {"company_name", "organization_name"}
                           and c["assertion_type"] in {"OBSERVED_FACT", "BUSINESS_CLAIM"} and c["sources"]]
            rank = store.get(business_id, "ranking", row.id)
            hypothesis = hypotheses.get(q["hypothesis_id"])
            candidates.append({
                "candidate_id": q["candidate_id"], "company_name": name_claims[0]["text"] if name_claims else None,
                "subject": evidence["subject"], "websites": sorted({s["final_url"] for s in evidence["sources"]}),
                "segment": hypothesis.audience_segment if hypothesis else None,
                "potential_need": q["assessment"]["need_evidence"],
                "sources_and_claims": evidence, "contradictions_and_review": q["review"],
                "contact": evidence.get("contact"), "qualification": q["gate"],
                "commercial_priority": rank, "assessment": q["assessment"],
            })
        traces = list(session.scalars(select(TraceRow).where(
            TraceRow.business_id == business_id, TraceRow.hypothesis_id.in_(hypothesis_ids))))
        rejections = list(session.scalars(select(RejectedTraceRow).where(
            RejectedTraceRow.business_id == business_id, RejectedTraceRow.trace_id.in_([t.id for t in traces]))))
        discoveries = [{"url": t.url, "query": t.query_used, "hypothesis_id": t.hypothesis_id,
                        "rejections": [r.reason for r in rejections if r.trace_id == t.id]} for t in traces]
    return {
        "business_id": business_id, "run": run, "commercial_effectiveness_proven": False,
        "business_dna": store.get(business_id, "dna", run.get("dna_version", "")),
        "market_research": store.get(business_id, "market", run.get("market_document_id", "")),
        "hypothesis_attribution": attribution,
        "reflection": [r for r in store.list(business_id, "reflection") if r.get("run_id") == run_id],
        "discoveries": discoveries,
        "candidates": sorted(candidates, key=lambda c: -(c["commercial_priority"] or {}).get("score", -1)),
        "notes": ["Null company_name means no explicit supported name claim; subject is retained.",
                  "Discoveries rejected before qualification are listed separately, not qualified leads."],
    }


def write_report(path: Path, report: dict) -> None:
    # Contains contact data. Never overwrite, print contacts, or publish automatically.
    with open(path, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
        json.dump(report, out, indent=2, ensure_ascii=False)
        out.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from evorove_lead.sqlalchemy_warehouse import engine_from_env
    engine = engine_from_env()
    try:
        report = build_report(engine, args.business_id, args.run_id)
        write_report(args.output, report)
    finally:
        engine.dispose()
    print(json.dumps({"exported": True, "candidates": len(report["candidates"])}))


if __name__ == "__main__":
    main()
