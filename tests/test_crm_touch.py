"""Payload-shape contract with evorove-crm's `LeadTouch.from_mapping`.

This repo cannot import evorove-crm (separate repo, separate deploy) so this
locks *our* side of the contract: the exact keys and constraints
`docs/cycle-1-contract.md` promises CRM's Cold ingest, spelled out here so a
change to `assembled_touch` that would break CRM's acceptance fails a test
in this repo, not silently in production.
"""

from __future__ import annotations

from evorove_lead.candidate import Candidate
from evorove_lead.crm_touch import assembled_touch
from evorove_lead.handoff import Cycle1Handoff

ALLOWED_SOURCES = frozenset({"evorove_lead", "evorove", "evorove_crm"})
ALLOWED_CYCLES = frozenset({1, 2, 3})


def test_assembled_touch_matches_crm_lead_touch_shape() -> None:
    candidate = Candidate(
        identity="Jordan Lee, jordan@example-bakery.com",
        reason="Posted looking for weekend catering for a family event.",
        reason_source="https://directory.example/jordan",
    )
    handoff = Cycle1Handoff.from_candidate(candidate, "email", person_id="ppl_abc123", hypothesis_id="hyp_1")

    payload = assembled_touch("tenant-a", candidate, handoff)

    # Required top-level fields (LeadTouch.__post_init__ / from_mapping).
    assert payload["schema_version"] == "1"
    assert isinstance(payload["touch_id"], str) and payload["touch_id"]
    assert payload["person_id"] == "ppl_abc123"
    assert payload["cycle"] in ALLOWED_CYCLES
    assert payload["cycle"] == 1
    assert payload["kind"] == "assembled"
    assert payload["source"] in ALLOWED_SOURCES
    assert isinstance(payload["summary"], str) and payload["summary"]

    # An "assembled" touch must already be addressable by phone or email --
    # CRM rejects it otherwise (LeadTouch.__post_init__, not_addressable).
    identity = payload["identity"]
    assert identity["email"] or identity["phone"]

    # The contract's Cold fields (docs/cycle-1-contract.md): reason,
    # reason_source, channel, hypothesis_id -- all metadata, no consent
    # field invented here (consent is checked by cycle 2 at send time).
    inner = payload["payload"]
    assert inner["reason"] == candidate.reason
    assert inner["reason_source"] == candidate.reason_source
    assert inner["channel"] == "email"
    assert inner["hypothesis_id"] == "hyp_1"
    assert "consent_basis" not in inner


def test_assembled_touch_person_id_is_stable_without_an_upstream_id() -> None:
    candidate = Candidate(
        identity="555-0100",
        reason="Publicly asked for weekend catering recommendations nearby.",
        reason_source="https://forum.example/thread/9",
    )
    handoff = Cycle1Handoff.from_candidate(candidate, "sms")

    payload = assembled_touch("tenant-a", candidate, handoff)

    assert payload["person_id"].startswith("ppl_")
    assert payload["identity"]["phone"] == "5550100"
