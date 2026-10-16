import json
import stat
import pytest
from evorove_lead.business import BusinessSeed
from evorove_lead.pilot_report import build_report, write_report
from test_research_pipeline import setup_pipeline


def test_dry_run_report_has_evidence_and_cannot_export_other_tenant(tmp_path):
    db, pipeline, engine, crm, deliveries = setup_pipeline(tmp_path, dry_run=True)
    engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    run_id = pipeline.last_run["id"]
    report = build_report(db, "a", run_id)
    assert report["business_dna"] and report["market_research"]
    source = report["business_dna"]["evidence"]["sources"][0]
    assert source["material_name"] == "site"
    assert source["source_type"] == "owner_material" and source["public"] is False
    assert report["discoveries"] and report["hypothesis_attribution"]
    candidate = report["candidates"][0]
    assert candidate["qualification"]["decision"] == "QUALIFIED"
    assert candidate["sources_and_claims"]["claims"] and candidate["contact"]
    assert candidate["commercial_priority"]
    assert report["commercial_effectiveness_proven"] is False
    assert deliveries.pending_count() == 0 and not crm.accepted
    with pytest.raises(ValueError, match="not found"):
        build_report(db, "other-tenant", run_id)
    path = tmp_path / "review.json"
    write_report(path, report)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["run"]["id"] == run_id
    with pytest.raises(FileExistsError):
        write_report(path, report)


def test_rejected_candidate_report_preserves_adversarial_decision(tmp_path):
    db, pipeline, engine, _, _ = setup_pipeline(tmp_path, dry_run=True, contradiction=True)
    engine.generate(BusinessSeed(site_url="https://seller.example", business_id="a"))
    report = build_report(db, "a", pipeline.last_run["id"])
    candidate = report["candidates"][0]
    assert candidate["commercial_priority"] is None
    assert "critical_contradiction" in candidate["qualification"]["codes"]
