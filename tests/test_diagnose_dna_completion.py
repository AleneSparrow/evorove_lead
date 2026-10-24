import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from evorove_lead.business_dna import BusinessDNA, BusinessDNAModelDraft
from evorove_lead.evidence import SourceSnapshot
from evorove_lead.market_intelligence import StructuredReasoner

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_dna_completion.py"
SPEC = importlib.util.spec_from_file_location("diagnose_dna_completion", SCRIPT)
diagnose = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnose)

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
PAGE_MARK = "UNIQUE_PAGE_SECRET_TOKEN_SHOULD_NEVER_PRINT"
KEY = "synthetic-test-key-must-not-leak"


def _source(**overrides):
    payload = dict(
        id="src_home",
        business_id="client-zero",
        url="https://evorove.com",
        final_url="https://evorove.com",
        content=f"Evorove finds fitting people from the open web. {PAGE_MARK}",
        fetched_at=NOW,
        source_type="first_party",
        subject="client-zero",
    )
    payload.update(overrides)
    return SourceSnapshot.model_validate(payload)


def _valid_dna_json():
    return json.dumps({
        "schema_version": 1,
        "business_id": "client-zero",
        "version": "v1",
        "site_url": "https://evorove.com",
        "evidence": {"business_id": "client-zero", "subject": "Evorove", "claims": []},
    })


def _opener(body, status=200):
    def open_(request, timeout):
        open_.calls.append((request, json.loads(request.data), timeout))
        payload = json.dumps(body).encode()
        stream = io.BytesIO(payload)
        stream.status = status
        return stream
    open_.calls = []
    return open_


def test_dna_prompt_matches_production_reasoner_and_engine():
    source = _source()
    system, user = diagnose.dna_system_and_user("client-zero", "https://evorove.com", (source,))
    captured = {}

    class Capture:
        def complete(self, *, system, user):
            captured["system"], captured["user"] = system, user
            return _valid_dna_json()

    StructuredReasoner(Capture()).ask(
        diagnose.DNA_STAGE,
        BusinessDNAModelDraft,
        dict(
            business_id="client-zero",
            site_url="https://evorove.com",
            version=BusinessDNA.version_for(source.content_hash),
            sources=[source.model_dump(mode="json")],
        ),
    )
    assert captured["system"] == system
    assert captured["user"] == user
    assert PAGE_MARK in user
    assert "BusinessDNAModelDraft" not in json.dumps(json.loads(user))


def test_request_payload_omits_thinking_and_optionally_sets_effort():
    bare = diagnose.request_payload("claude-sonnet-5-5", "sys", "usr", 4000, None)
    assert "thinking" not in bare and "output_config" not in bare
    assert bare["max_tokens"] == 4000
    limited = diagnose.request_payload("claude-sonnet-5-5", "sys", "usr", 4000, "low")
    assert limited["output_config"] == {"effort": "low"}
    assert "thinking" not in limited


def test_diagnose_keeps_truncation_metrics_without_bodies():
    body = {
        "stop_reason": "max_tokens",
        "content": [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": '{"schema_version": 1, "business_id":'},
        ],
        "usage": {
            "input_tokens": 5120,
            "output_tokens": 4000,
            "output_tokens_details": {"thinking_tokens": 3188},
        },
    }
    report = diagnose.diagnose_response(200, body, max_tokens=4000, effort=None)
    assert report["stop_reason"] == "max_tokens"
    assert report["thinking_tokens"] == 3188
    assert report["output_tokens"] == 4000
    assert report["text_chars"] == 36
    assert report["json_valid"] is False
    assert report["content_block_types"] == {"thinking": 1, "text": 1}
    assert report["cost_published_2_10_usd"] == pytest.approx(0.05024)
    assert report["cost_conservative_3_15_usd"] == pytest.approx(0.07536)
    assert PAGE_MARK not in json.dumps(report)


def test_valid_json_is_checked_against_dna_schema():
    body = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": _valid_dna_json()}],
        "usage": {"input_tokens": 100, "output_tokens": 80},
    }
    report = diagnose.diagnose_response(200, body, max_tokens=4000, effort="low")
    assert report["json_valid"] is True
    assert report["thinking_tokens"] is None
    assert report["effort"] == "low"


def test_http_400_keeps_type_and_drops_provider_message():
    def fail(request, timeout):
        body = json.dumps({
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": f"thinking disabled leaked {KEY} and {PAGE_MARK}",
            },
        }).encode()
        raise HTTPError(request.full_url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(body))
    status, body, transport = diagnose.post_messages(
        KEY, diagnose.request_payload("claude-sonnet-5-5", "s", "u", 4000, None), timeout=30, opener=fail)
    assert status == 400 and transport is None
    report = diagnose.diagnose_response(status, body, max_tokens=4000, effort=None)
    dumped = json.dumps(report)
    assert report["provider_error_type"] == "invalid_request_error"
    assert KEY not in dumped and PAGE_MARK not in dumped and "leaked" not in dumped


def test_main_one_mocked_call_prints_only_safe_metrics(tmp_path, capsys, monkeypatch):
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([_source().model_dump(mode="json")]), encoding="utf-8")
    opener = _opener({
        "stop_reason": "max_tokens",
        "content": [{"type": "text", "text": '{"truncated": true'}],
        "usage": {
            "input_tokens": 4800,
            "output_tokens": 4000,
            "output_tokens_details": {"thinking_tokens": 2900},
        },
    })
    code = diagnose.main(
        ["--max-tokens", "4000", "--effort", "low", "--sources-json", str(sources_path)],
        opener=opener,
        environ={"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_MODEL": "claude-sonnet-5-5"},
        load_sources=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("live fetch must not run")),
    )
    out = capsys.readouterr().out
    report = json.loads(out)
    assert code == 0
    assert report["stop_reason"] == "max_tokens"
    assert report["thinking_tokens"] == 2900
    assert report["effort"] == "low"
    assert opener.calls[0][1]["output_config"] == {"effort": "low"}
    assert "thinking" not in opener.calls[0][1]
    assert opener.calls[0][0].get_header("X-api-key") == KEY
    assert KEY not in out and PAGE_MARK not in out
    assert "Return only JSON" not in out
    assert diagnose.DNA_STAGE not in out


def test_main_matches_current_payload_when_effort_omitted(tmp_path, capsys):
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([_source().model_dump(mode="json")]), encoding="utf-8")
    opener = _opener({
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": _valid_dna_json()}],
        "usage": {"input_tokens": 10, "output_tokens": 12},
    })
    diagnose.main(
        ["--max-tokens", "4000", "--sources-json", str(sources_path)],
        opener=opener,
        environ={"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_MODEL": "claude-sonnet-5-5"},
    )
    payload = opener.calls[0][1]
    assert payload["max_tokens"] == 4000
    assert "thinking" not in payload and "output_config" not in payload
    assert json.loads(capsys.readouterr().out)["json_valid"] is True


def test_missing_key_and_transport_errors_stay_generic(capsys):
    code = diagnose.main(["--max-tokens", "4000"], environ={}, load_sources=lambda *a, **k: (_source(),))
    missing = capsys.readouterr().out
    assert code == 2
    assert json.loads(missing) == {"error": "ANTHROPIC_API_KEY missing"}

    def fail(request, timeout):
        raise URLError("sensitive provider host details")
    report = diagnose.run(
        diagnose.parse_args(["--max-tokens", "8000", "--effort", "low"]),
        opener=fail,
        environ={"ANTHROPIC_API_KEY": KEY},
        load_sources=lambda *a, **k: (_source(),),
    )
    assert report == {"error": "URLError", "http_status": None, "max_tokens": 8000, "effort": "low"}
    assert "sensitive" not in json.dumps(report)


@pytest.mark.parametrize("value", ["0", "255", "128001"])
def test_max_tokens_bounds(value):
    with pytest.raises(SystemExit):
        diagnose.parse_args(["--max-tokens", value])


def test_full_dna_path_reports_assemble_errors_without_values():
    source = _source()
    empty = diagnose.full_dna_validation(
        _valid_dna_json(), business_id="client-zero", site_url="https://evorove.com", sources=(source,))
    assert empty["json_valid"] is True
    assert empty["assemble_valid"] is False
    assert empty["grounding_valid"] is False
    assert empty["validation"]["schema"] == "BusinessDNA"
    assert empty["validation"]["error_count"] >= 1
    assert all(set(item) == {"loc", "type"} for item in empty["validation"]["errors"])
    assert PAGE_MARK not in json.dumps(empty)

    invented = {
        "schema_version": 1,
        "business_id": "client-zero",
        "version": BusinessDNA.version_for(source.content_hash),
        "site_url": "https://evorove.com",
        "service_country": "US",
        "evidence": {
            "business_id": "client-zero",
            "subject": "client-zero",
            "claims": [{
                "id": "svc",
                "subject": "client-zero",
                "predicate": "offers",
                "text": "invented",
                "assertion_type": "BUSINESS_CLAIM",
                "confidence": 0.8,
                "confidence_reason": "model invention",
                "sources": [{"source_id": "src_home", "quote": "INVENTED_QUOTE_SECRET", "relationship": "supports"}],
                "slots": ["service", "geography", "customer_category"],
            }],
        },
    }
    bound = diagnose.full_dna_validation(
        json.dumps(invented), business_id="client-zero", site_url="https://evorove.com", sources=(source,))
    assert bound["json_valid"] is True
    assert bound["assemble_valid"] is False
    assert "INVENTED_QUOTE_SECRET" not in json.dumps(bound)
    assert PAGE_MARK not in json.dumps(bound)
