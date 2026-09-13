from evorove_lead.offer_reader import read_offer
from evorove_lead.presence import page_material


BAKERY_HTML = """
<html>
  <head><title>Sunrise Bakery</title></head>
  <body>
    <script>var secret = "ignore";</script>
    <h1>Weekend catering for local events</h1>
    <p>We cook for busy parents in town. Trays start at $49.</p>
  </body>
</html>
"""

SITE = "https://sunrise-bakery.example/"


def test_reads_service_audience_and_quoted_price_from_owner_site() -> None:
    offer = read_offer((page_material(SITE, BAKERY_HTML),))

    assert offer.what_we_sell[0].text == "Weekend catering"
    assert any(claim.text.lower() == "busy parents" for claim in offer.who_may_fit)
    assert any(claim.kind == "price" and claim.text == "$49" for claim in offer.commercial_claims)
    assert "guarantee" in offer.must_not_promise


def test_script_tags_are_not_treated_as_the_offer() -> None:
    offer = read_offer((page_material(SITE, BAKERY_HTML),))
    combined = " ".join(claim.text for claim in offer.what_we_sell).lower()
    assert "ignore" not in combined


NEGATED_AUDIENCE_HTML = """
<html>
  <head><title>Evorove</title></head>
  <body>
    <h1>Weekend catering for local events</h1>
    <p>We cook for busy parents in town.</p>
    <p>Nobody builds a version for your company, and there are no keyword lists.</p>
  </body>
</html>
"""


def test_negated_for_phrase_is_not_read_as_an_audience_claim() -> None:
    """Real bug found piloting evorove.com: "Nobody builds ... for your
    company" is a denial, not an audience claim -- the naive "for X"
    pattern used to grab "your company" from it anyway."""

    offer = read_offer((page_material(SITE, NEGATED_AUDIENCE_HTML),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "your company" not in audience_texts
    assert "busy parents" in audience_texts


def test_negation_a_few_words_further_back_is_still_caught() -> None:
    html = (
        "<html><head><title>T</title></head><body>"
        "<h1>Weekend catering for local events</h1>"
        "<p>We do not currently build anything custom for enterprise clients.</p>"
        "</body></html>"
    )

    offer = read_offer((page_material(SITE, html),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "enterprise clients" not in audience_texts


def test_a_real_for_phrase_near_an_unrelated_negation_still_matches() -> None:
    """The lookback window shouldn't blank out a genuine audience claim just
    because some negation appeared earlier in the same sentence fragment."""

    html = (
        "<html><head><title>T</title></head><body>"
        "<h1>Weekend catering for local events</h1>"
        "<p>We never skip a booking. We cater for busy parents.</p>"
        "</body></html>"
    )

    offer = read_offer((page_material(SITE, html),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "busy parents" in audience_texts
