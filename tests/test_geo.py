from evorove_lead.geo import infer_geo_radius
from evorove_lead.materials import DepositedMaterial


def _material(body: str) -> DepositedMaterial:
    return DepositedMaterial(name="site", body=body)


def test_infers_locality_after_in():
    radius = infer_geo_radius((_material("Weekend catering in Austin for busy parents."),))
    assert radius.locality == "Austin"
    assert radius.country == "US"


def test_infers_locality_with_state_after_comma():
    radius = infer_geo_radius((_material("Proudly serving Austin, TX since 2015."),))
    assert radius.locality == "Austin, TX"


def test_recognizes_based_in_and_located_in():
    assert infer_geo_radius((_material("Based in Round Rock for ten years."),)).locality == "Round Rock"
    assert infer_geo_radius((_material("Located in Cedar Park."),)).locality == "Cedar Park"


def test_lowercase_in_town_does_not_match():
    """"in town", "in fact", "in 2020" aren't cities -- capitalization is required."""

    radius = infer_geo_radius((_material("We cook for busy parents in town."),))
    assert radius.locality == ""


def test_no_match_returns_whole_us_default():
    radius = infer_geo_radius((_material("Weekend catering for local events."),))
    assert radius == infer_geo_radius(())
    assert radius.locality == ""
    assert radius.country == "US"


def test_sign_in_nav_link_is_not_read_as_a_locality():
    """Real bug found running the first live client-0 pilot: a nav bar has
    no separator between adjacent links ("...FAQ Sign in Start free
    trial..."), so "in" from "Sign in" ran straight into "Start" from the
    next button and read as a fabricated locality, "Start"."""

    radius = infer_geo_radius(
        (_material("FAQ Sign in Start free trial. Weekend catering for local events."),)
    )
    assert radius.locality == ""


def test_own_board_vocabulary_is_not_read_as_a_locality():
    """Real bug found running the client-0 pilot: evorove.com's own copy
    talks about its product ("a person land in Cold"), and this product's
    own board tab name ("Cold") is not a city, even capitalized after
    "in". Keeps checking the rest of the text instead of stopping there."""

    radius = infer_geo_radius(
        (_material("Only then does a person land in Cold. Based in Austin since 2015."),)
    )
    assert radius.locality == "Austin"


def test_checks_materials_in_order_and_stops_at_first_hit():
    radius = infer_geo_radius(
        (
            _material("Nothing here."),
            _material("Serving Denver and the surrounding area."),
            _material("Also near Boulder."),
        )
    )
    # Lowercase "and" ends the capitalized-word run; only "Denver" matches.
    # The second material (Boulder) is never reached.
    assert radius.locality == "Denver"
