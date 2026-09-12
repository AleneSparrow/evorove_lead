import pytest
from fastapi.testclient import TestClient

from evorove_lead.api import create_app
from evorove_lead.warehouse import RecordingAnalysisWarehouse

SECRET_HEADER = "X-Internal-Task-Secret"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("INTERNAL_TASK_SECRET", "local_development_only")


def _client(warehouse=None) -> tuple[TestClient, RecordingAnalysisWarehouse]:
    store = warehouse if warehouse is not None else RecordingAnalysisWarehouse()
    return TestClient(create_app(store)), store


def test_records_a_valid_outcome_event():
    client, warehouse = _client()

    response = client.post(
        "/api/v1/internal/hypothesis-outcomes",
        json={
            "business_id": "tenant-a",
            "hypothesis_id": "hyp-1",
            "case_id": "case-1",
            "outcome": "done",
        },
        headers={SECRET_HEADER: "local_development_only"},
    )

    assert response.status_code == 202
    assert len(warehouse.hypothesis_outcomes) == 1
    saved = warehouse.hypothesis_outcomes[0]
    assert saved.business_id == "tenant-a"
    assert saved.hypothesis_id == "hyp-1"
    assert saved.case_id == "case-1"
    assert saved.outcome == "done"


def test_rejects_missing_secret():
    client, warehouse = _client()

    response = client.post(
        "/api/v1/internal/hypothesis-outcomes",
        json={"business_id": "t", "hypothesis_id": "h", "case_id": "c", "outcome": "done"},
    )

    assert response.status_code == 401
    assert warehouse.hypothesis_outcomes == []


def test_rejects_wrong_secret():
    client, warehouse = _client()

    response = client.post(
        "/api/v1/internal/hypothesis-outcomes",
        json={"business_id": "t", "hypothesis_id": "h", "case_id": "c", "outcome": "done"},
        headers={SECRET_HEADER: "wrong"},
    )

    assert response.status_code == 401
    assert warehouse.hypothesis_outcomes == []


def test_rejects_unknown_outcome_value():
    client, warehouse = _client()

    response = client.post(
        "/api/v1/internal/hypothesis-outcomes",
        json={"business_id": "t", "hypothesis_id": "h", "case_id": "c", "outcome": "won"},
        headers={SECRET_HEADER: "local_development_only"},
    )

    assert response.status_code == 422
    assert warehouse.hypothesis_outcomes == []


def test_rejects_blank_ids():
    client, warehouse = _client()

    response = client.post(
        "/api/v1/internal/hypothesis-outcomes",
        json={"business_id": "", "hypothesis_id": "h", "case_id": "c", "outcome": "done"},
        headers={SECRET_HEADER: "local_development_only"},
    )

    assert response.status_code == 422
    assert warehouse.hypothesis_outcomes == []


def test_disabled_when_no_secret_configured(monkeypatch):
    monkeypatch.delenv("INTERNAL_TASK_SECRET", raising=False)
    client, warehouse = _client()

    response = client.post(
        "/api/v1/internal/hypothesis-outcomes",
        json={"business_id": "t", "hypothesis_id": "h", "case_id": "c", "outcome": "done"},
        headers={SECRET_HEADER: "anything"},
    )

    assert response.status_code == 401
    assert warehouse.hypothesis_outcomes == []


def test_no_pii_fields_are_accepted_by_the_schema():
    """The request schema itself has no place to put a name, email, or message."""

    from evorove_lead.api import HypothesisOutcomeIn

    assert set(HypothesisOutcomeIn.model_fields) == {
        "business_id",
        "hypothesis_id",
        "case_id",
        "outcome",
    }
