from evorove_lead.offer import GroundedClaim, OfferUnderstanding
from evorove_lead.policy import LeadGenerationPolicy


def test_may_not_seek_people_without_offer_or_source() -> None:
    policy = LeadGenerationPolicy()
    offer = OfferUnderstanding(
        what_we_sell=(GroundedClaim(text="Weekend catering", source_name="site"),),
        who_may_fit=(GroundedClaim(text="busy parents", source_name="site"),),
        commercial_claims=(),
        must_not_promise=("price", "discount", "guarantee", "legal_claim"),
    )

    assert policy.may_read_presence("https://sunrise-bakery.example/")
    assert not policy.may_read_presence("  ")
    assert not policy.may_seek_people(offer=None, search_connected=True)
    assert not policy.may_seek_people(offer=offer, search_connected=False)
    assert policy.may_seek_people(offer=offer, search_connected=True)
