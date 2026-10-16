import json

import pytest

from evorove_lead import run
from evorove_lead.crm_touch import RecordingLeadTouchSink


BRIEF = "Weekend catering for local events\n\nWe cook for busy parents in town.\n"
PEOPLE = (
    '{"name":"Jordan Lee","email":"jordan@example-bakery.com",'
    '"observed_fact":"Already advertises weekend catering to nearby families.",'
    '"observed_source":"owner-copied public post, 2026-09-01","channel":"email"}\n'
    '{"name":"Pat Dump","phone":"+15550100999","observed_fact":"Has a phone number in a purchased list.",'
    '"observed_source":"phone dump","channel":"sms"}\n'
)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name in ("CRM_BASE_URL", "INTERNAL_TASK_SECRET", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    materials = tmp_path / "materials"
    observations = tmp_path / "observations"
    materials.mkdir()
    observations.mkdir()
    (materials / "site.md").write_text(BRIEF, encoding="utf-8")
    (observations / "people.jsonl").write_text(PEOPLE, encoding="utf-8")
    return materials, observations


def _run(capsys, *argv: str) -> dict:
    assert run.main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


def test_run_rejects_unverified_observations_and_dump(dirs, capsys, monkeypatch) -> None:
    materials, observations = dirs
    sink = RecordingLeadTouchSink()
    monkeypatch.setattr("evorove_lead.engine.sink_from_env", lambda: sink)

    summary = _run(
        capsys,
        "--business-id", "acme-home-services",
        "--site-url", "https://sunrise-bakery.example/",
        "--materials", str(materials),
        "--observations", str(observations),
    )

    assert summary["status"] == "no_fit"
    assert summary["cold"] == 0
    assert [item["why"] for item in summary["rejected"]] == ["missing_verified_evidence", "reason is not tied to the offer"]
    assert sink.published == []


def test_run_without_a_people_source_stops_after_the_offer(dirs, capsys) -> None:
    materials, _ = dirs
    summary = _run(
        capsys,
        "--business-id", "acme-home-services",
        "--site-url", "https://sunrise-bakery.example/",
        "--materials", str(materials),
    )

    assert summary == {
        "status": "search_unconnected",
        "offer_understood": True,
        "cold": 0,
        "rejected": [],
        "crm_redelivered": 0,
        "crm_pending": 0,
    }
