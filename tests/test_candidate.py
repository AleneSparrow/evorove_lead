import pytest

from evorove_lead import Candidate, CandidateRejected, accept_candidate


def test_accepts_identity_reason_and_source() -> None:
    candidate = accept_candidate(
        identity="Jordan Lee, owner@example-bakery.com",
        reason="Runs a neighborhood bakery that already advertises weekend catering.",
        reason_source="owner-materials/service-description.txt: weekend catering for local events",
    )

    assert candidate == Candidate(
        identity="Jordan Lee, owner@example-bakery.com",
        reason="Runs a neighborhood bakery that already advertises weekend catering.",
        reason_source="owner-materials/service-description.txt: weekend catering for local events",
    )


@pytest.mark.parametrize(
    ("identity", "reason", "reason_source", "missing"),
    [
        ("", "Fits the stated audience.", "owner-materials/ad-copy.txt", "identity"),
        ("  ", "Fits the stated audience.", "owner-materials/ad-copy.txt", "identity"),
        ("Jordan Lee", "", "owner-materials/ad-copy.txt", "reason"),
        ("Jordan Lee", "   ", "owner-materials/ad-copy.txt", "reason"),
        ("Jordan Lee", "Fits the stated audience.", "", "reason_source"),
        ("Jordan Lee", "Fits the stated audience.", "\n", "reason_source"),
    ],
)
def test_rejects_blank_required_fields(
    identity: str, reason: str, reason_source: str, missing: str
) -> None:
    with pytest.raises(CandidateRejected, match=f"{missing} is required"):
        accept_candidate(identity=identity, reason=reason, reason_source=reason_source)


def test_contact_without_reason_is_not_a_candidate() -> None:
    with pytest.raises(CandidateRejected, match="reason is required"):
        accept_candidate(
            identity="555-0100",
            reason="",
            reason_source="phone dump",
        )
