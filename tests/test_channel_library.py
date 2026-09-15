from evorove_lead.channel_library import categorize, channels_for_archetype


def test_categorize_matches_known_keyword_case_insensitively():
    assert categorize("Family Law Practice") == "professional_services"
    assert categorize("weekend catering") == "food_hospitality"
    assert categorize("Downtown Barbershop") == "local_service"
    assert categorize("B2B SaaS platform") == "b2b_software"


def test_categorize_returns_empty_for_unrecognized_archetype():
    assert categorize("interpretive dance troupe") == ""
    assert categorize("") == ""


def test_channels_for_archetype_returns_nothing_for_blank_archetype():
    assert channels_for_archetype("") == ()
    assert channels_for_archetype("   ") == ()


def test_channels_for_archetype_returns_nothing_for_unrecognized_archetype():
    assert channels_for_archetype("interpretive dance troupe") == ()


def test_channels_for_archetype_returns_seed_channels_for_a_known_category():
    channels = channels_for_archetype("bakery")

    assert channels  # non-empty
    names = {c.channel for c in channels}
    assert "instagram" in names
    assert "yelp" in names
    # Every target is a site-restricted query, not a new connector/API.
    for target in channels:
        assert target.site_filter.startswith("site:")
        assert target.reason.strip()


def test_different_categories_get_different_channel_sets():
    local_service_channels = {c.channel for c in channels_for_archetype("nail salon")}
    professional_channels = {c.channel for c in channels_for_archetype("family law practice")}

    assert local_service_channels != professional_channels
    assert "yelp" in local_service_channels
    assert "linkedin" in professional_channels
