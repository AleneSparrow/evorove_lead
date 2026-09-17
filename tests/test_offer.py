from pathlib import Path

import pytest

from evorove_lead import (
    Candidate,
    CandidateRejected,
    CommercialClaim,
    GroundedClaim,
    OfferRejected,
    accept_candidate_for_offer,
    accept_offer_understanding,
    load_deposited_materials,
)


def _write_ad(root: Path) -> None:
    (root / "ad-copy.txt").write_text(
        "Weekend catering for local events. For busy parents in town.\n",
        encoding="utf-8",
    )


def test_accepts_offer_quoted_from_deposited_copy(tmp_path: Path) -> None:
    _write_ad(tmp_path)
    materials = load_deposited_materials(tmp_path)

    offer = accept_offer_understanding(
        materials=materials,
        what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
        who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
    )

    assert offer.must_not_promise == ("price", "discount", "guarantee", "legal_claim")
    assert offer.commercial_claims == ()


def test_rejects_offer_without_what_or_who(tmp_path: Path) -> None:
    _write_ad(tmp_path)
    materials = load_deposited_materials(tmp_path)

    with pytest.raises(OfferRejected, match="what_we_sell is required"):
        accept_offer_understanding(
            materials=materials,
            what_we_sell=[],
            who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
        )
    with pytest.raises(OfferRejected, match="who_may_fit is required"):
        accept_offer_understanding(
            materials=materials,
            what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
            who_may_fit=[],
        )


def test_rejects_offer_without_materials() -> None:
    with pytest.raises(OfferRejected, match="materials are required"):
        accept_offer_understanding(
            materials=(),
            what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
            who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
        )


def test_rejects_invented_audience(tmp_path: Path) -> None:
    _write_ad(tmp_path)
    materials = load_deposited_materials(tmp_path)

    with pytest.raises(OfferRejected, match="claim is not in ad-copy.txt"):
        accept_offer_understanding(
            materials=materials,
            what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
            who_may_fit=[GroundedClaim(text="enterprise legal teams", source_name="ad-copy.txt")],
        )


def test_rejects_invented_price(tmp_path: Path) -> None:
    _write_ad(tmp_path)
    materials = load_deposited_materials(tmp_path)

    with pytest.raises(OfferRejected, match="claim is not in ad-copy.txt"):
        accept_offer_understanding(
            materials=materials,
            what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
            who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
            commercial_claims=[
                CommercialClaim(kind="price", text="$49 intro", source_name="ad-copy.txt"),
            ],
        )


def test_accepts_price_only_when_owner_wrote_it(tmp_path: Path) -> None:
    (tmp_path / "ad-copy.txt").write_text(
        "Weekend catering for busy parents. Trays start at $49.\n",
        encoding="utf-8",
    )
    materials = load_deposited_materials(tmp_path)

    offer = accept_offer_understanding(
        materials=materials,
        what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
        who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
        commercial_claims=[
            CommercialClaim(kind="price", text="$49", source_name="ad-copy.txt"),
        ],
    )

    assert offer.must_not_promise == ("discount", "guarantee", "legal_claim")


def test_url_file_is_not_fetched_and_cannot_invent_copy(tmp_path: Path) -> None:
    (tmp_path / "site-url.txt").write_text("https://example-bakery.example/\n", encoding="utf-8")
    materials = load_deposited_materials(tmp_path)

    with pytest.raises(OfferRejected, match="claim is not in site-url.txt"):
        accept_offer_understanding(
            materials=materials,
            what_we_sell=[GroundedClaim(text="Weekend catering", source_name="site-url.txt")],
            who_may_fit=[GroundedClaim(text="busy parents", source_name="site-url.txt")],
        )


def test_candidate_reason_must_tie_to_offer(tmp_path: Path) -> None:
    _write_ad(tmp_path)
    offer = accept_offer_understanding(
        materials=load_deposited_materials(tmp_path),
        what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
        who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
    )

    accepted = accept_candidate_for_offer(
        identity="Jordan Lee, owner@example-bakery.com",
        reason="Already advertises weekend catering to nearby families.",
        reason_source="ad-copy.txt",
        offer=offer,
    )
    assert accepted == Candidate(
        identity="Jordan Lee, owner@example-bakery.com",
        reason="Already advertises weekend catering to nearby families.",
        reason_source="ad-copy.txt",
    )

    with pytest.raises(CandidateRejected, match="not tied to the offer"):
        accept_candidate_for_offer(
            identity="555-0100",
            reason="Has a phone number in a purchased list.",
            reason_source="phone dump",
            offer=offer,
        )


def test_candidate_reason_rejects_the_offer_pasted_verbatim(tmp_path: Path) -> None:
    """A dump: the client's own slogan standing in for a fact about the person."""

    _write_ad(tmp_path)
    offer = accept_offer_understanding(
        materials=load_deposited_materials(tmp_path),
        what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
        who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
    )

    with pytest.raises(CandidateRejected, match="offer's own text"):
        accept_candidate_for_offer(
            identity="555-0100",
            reason="Weekend catering",
            reason_source="ad-copy.txt",
            offer=offer,
        )
    with pytest.raises(CandidateRejected, match="offer's own text"):
        accept_candidate_for_offer(
            identity="555-0100",
            reason="  weekend catering  ",
            reason_source="ad-copy.txt",
            offer=offer,
        )


def test_candidate_reason_need_not_quote_the_offer_verbatim(tmp_path: Path) -> None:
    """A real fact from the open web describes the person, not the brief's exact words."""

    _write_ad(tmp_path)
    offer = accept_offer_understanding(
        materials=load_deposited_materials(tmp_path),
        what_we_sell=[GroundedClaim(text="Weekend catering", source_name="ad-copy.txt")],
        who_may_fit=[GroundedClaim(text="busy parents", source_name="ad-copy.txt")],
    )

    accepted = accept_candidate_for_offer(
        identity="Jordan Lee, owner@example-bakery.com",
        reason="Posted looking for a caterer for their kid's weekend birthday party.",
        reason_source="community-board.example/post/482",
        offer=offer,
    )
    assert accepted.reason == "Posted looking for a caterer for their kid's weekend birthday party."
