"""Saved-text extraction. The scripted completion never opens a socket."""
import json

from evorove_lead.field_extraction import (
    DEFAULT_MAX_RUN_USD, EFFORT, MAX_OUTPUT_TOKENS, MODEL, SavedTextExtractor, ScriptedExtraction, main,
)


TEXT = "Jane Doe is the founder. Northwind is a marketing agency for B2B teams. The team of 12 people is in Ohio."


def _reply(**payload):
    body = {
        "name": "Jane Doe",
        "role": "founder",
        "quote": "Northwind is a marketing agency for B2B teams.",
        "team_size": "12",
        "country": "Ohio",
    }
    body.update(payload)
    script = ScriptedExtraction(json.dumps(body))
    return script


def test_quote_must_be_a_substring_and_the_call_is_cached(monkeypatch):
    monkeypatch.delenv("QUALIFICATION_EXTRACT_MAX_USD", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    script = _reply()
    extractor = SavedTextExtractor(script)
    assert extractor.model == MODEL and extractor.effort == EFFORT
    assert extractor.max_output_tokens == MAX_OUTPUT_TOKENS == 300
    assert extractor.max_run_usd == DEFAULT_MAX_RUN_USD == 0.5
    first = extractor.extract("northwind.example", TEXT)
    assert script.calls == 1
    assert first["called"] is True
    assert first["quote"] == "Northwind is a marketing agency for B2B teams."
    assert len(first["quote"]) <= 280
    assert first["quote"] in TEXT
    assert first["team_size"] == "12" and first["country"] == "Ohio" and first["name"] == "Jane Doe"
    second = extractor.extract("other.example", TEXT)
    assert script.calls == 1 and second["cache_hit"] is True and second["called"] is False
    other = extractor.extract("northwind.example", TEXT + " A second page.")
    assert script.calls == 1 and other["reason"] == "already_called"


def test_ungrounded_fields_are_dropped_and_an_unknown_price_blocks_the_call():
    invented = _reply(quote="We guarantee ten meetings a month.", team_size="4000", country="France", name="Pat Invited", role="wizard")
    dropped = SavedTextExtractor(invented).extract("northwind.example", TEXT)
    assert invented.calls == 1
    assert dropped["quote"] == "" and dropped["team_size"] == "" and dropped["country"] == ""
    assert dropped["name"] == "" and dropped["role"] == ""

    class Boom:
        def complete(self, **kwargs):
            raise AssertionError("unknown price must not call")

    blocked = SavedTextExtractor(Boom(), model="claude-made-up-9").extract("northwind.example", TEXT)
    assert blocked["called"] is False and blocked["reason"] == "unknown_model_price"

    tight = SavedTextExtractor(_reply(), max_run_usd=0)
    capped = tight.extract("northwind.example", TEXT)
    assert capped["reason"] == "run_budget" and capped["called"] is False
    assert main([]) == 2
