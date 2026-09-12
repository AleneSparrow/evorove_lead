import json
from pathlib import Path

import pytest

from evorove_lead.offer import GroundedClaim, OfferUnderstanding
from evorove_lead.observations import (
    ObservationRejected,
    OwnerObservationPeopleSearch,
    load_owner_observations,
    observation_to_hit,
)
from evorove_lead.search import PeopleHit


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_loads_reasoned_observation_and_skips_contact_dump(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "known-people.jsonl",
        [
            {
                "name": "Jordan Lee",
                "email": "jordan@example-bakery.com",
                "observed_fact": "Already advertises weekend catering to nearby families.",
                "observed_source": "owner-copied public post, 2026-09-01",
                "channel": "email",
            },
            {
                "phone": "555-0100999",
                "channel": "sms",
            },
            {
                "name": "Pat Dump",
                "phone": "+15550100999",
                "observed_fact": "",
                "observed_source": "purchased list",
                "channel": "sms",
            },
        ],
    )
    (tmp_path / "README.md").write_text("not a person\n", encoding="utf-8")

    hits = load_owner_observations(tmp_path)

    assert hits == (
        PeopleHit(
            identity="Jordan Lee, jordan@example-bakery.com",
            observed_fact="Already advertises weekend catering to nearby families.",
            observed_source="owner-copied public post, 2026-09-01",
            channel="email",
        ),
    )


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ObservationRejected, match="directory is required"):
        load_owner_observations(tmp_path / "missing")


def test_refuses_to_load_env_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=no\n", encoding="utf-8")

    with pytest.raises(ObservationRejected, match="secrets"):
        load_owner_observations(tmp_path)


def test_phone_dump_without_fact_is_not_a_hit() -> None:
    assert (
        observation_to_hit(
            {
                "phone": "+15550100999",
                "channel": "sms",
                "observed_source": "purchased list",
            }
        )
        is None
    )


def test_connected_search_returns_deposited_hits(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "people.jsonl",
        [
            {
                "name": "Sam Rivera",
                "email": "sam@parents.example",
                "observed_fact": "Writes a newsletter for busy parents.",
                "observed_source": "person the owner already knows",
                "channel": "email",
            }
        ],
    )
    offer = OfferUnderstanding(
        what_we_sell=(GroundedClaim(text="Weekend catering", source_name="site"),),
        who_may_fit=(GroundedClaim(text="busy parents", source_name="site"),),
        commercial_claims=(),
        must_not_promise=("price", "discount", "guarantee", "legal_claim"),
    )
    search = OwnerObservationPeopleSearch(tmp_path)

    assert search.connected is True
    hits = search.find(offer)
    assert len(hits) == 1
    assert hits[0].identity.startswith("Sam Rivera")
