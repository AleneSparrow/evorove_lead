from types import SimpleNamespace
import pytest
from evorove_lead.evaluation import wilson, evaluate, validate_dataset, main

def test_empty_does_not_report_perfect_precision_or_zero_cost():
    metrics = evaluate([])
    assert metrics["precision"] is None and metrics["recall"] is None
    assert metrics["cost_per_qualified_usd"] is None
    assert metrics["positive_reply_rate"] is None
    assert not validate_dataset([])["representative_dataset_available"]

def test_confidence_interval_and_leakage():
    low, high = wilson(90, 100)
    assert .82 < low < .84 and .94 < high < .95
    case = dict(profile="SMB", buyer_type="B2B", provenance="synthetic", company_key="same", document_hashes=("doc",))
    with pytest.raises(ValueError, match="leakage"):
        validate_dataset([SimpleNamespace(id="a", split="development", **case), SimpleNamespace(id="b", split="holdout", **case)])

def test_missing_dataset_is_blocker(tmp_path, capsys):
    assert main(["--dataset", str(tmp_path / "not-provided.jsonl")]) == 2
    assert "labelled_dataset_missing" in capsys.readouterr().out
