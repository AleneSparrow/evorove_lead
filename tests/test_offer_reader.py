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


ALL_CAPS_HERO_HTML = """
<html>
  <head><title>Sunrise Bakery</title></head>
  <body>
    <h1>COLD TRAYS. HOT EVENTS.</h1>
    <h2>Weekend catering for local events.</h2>
    <p>We cook for busy parents in town.</p>
  </body>
</html>
"""


def test_all_caps_hero_heading_is_not_read_as_the_offer() -> None:
    """Real bug found running the first live client-0 pilot: evorove.com's
    own H1 is a shouted tagline ("COLD IN. DONE ON THE BOARD."), not a
    description of what is sold. Taking it verbatim seeded the offer's
    vocabulary with ordinary English words that collide with unrelated
    pages -- reading past it to the next real heading instead."""

    offer = read_offer((page_material(SITE, ALL_CAPS_HERO_HTML),))

    assert offer.what_we_sell[0].text != "COLD TRAYS. HOT EVENTS."
    assert offer.what_we_sell[0].text == "Weekend catering"


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


def test_four_word_audience_phrase_is_no_longer_dropped() -> None:
    """Real case from piloting evorove.com's own copy: the old three-word
    cap silently dropped "small local service businesses" (four words)."""

    html = (
        "<html><head><title>T</title></head><body>"
        "<h1>Weekend catering for local events</h1>"
        "<p>Evorove finds, sells, and closes leads for small local service businesses.</p>"
        "</body></html>"
    )

    offer = read_offer((page_material(SITE, html),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "small local service businesses" in audience_texts


def test_serves_trigger_word_without_the_word_for() -> None:
    html = (
        "<html><head><title>T</title></head><body>"
        "<h1>Weekend catering for local events</h1>"
        "<p>Evorove serves small businesses that want more customers.</p>"
        "</body></html>"
    )

    offer = read_offer((page_material(SITE, html),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "small businesses" in audience_texts


def test_faq_question_heading_is_not_read_as_an_audience_claim() -> None:
    """Real bug found running the first live client-0 pilot on evorove.com:
    its FAQ headings are full of "for X" inside a question ("What does
    Done mean for an offline vs online business?"), which is a fragment
    of the question, not a declared audience."""

    html = (
        "<html><head><title>T</title></head><body>"
        "<h1>Weekend catering for local events</h1>"
        "<p>We cook for busy parents.</p>"
        "<p>What does Done mean for an offline vs online business?</p>"
        "</body></html>"
    )

    offer = read_offer((page_material(SITE, html),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "an offline vs online business" not in audience_texts
    assert "busy parents" in audience_texts


def test_self_referential_filler_is_not_read_as_an_audience_claim() -> None:
    """Real bug from the same pilot: "a real hour for the service you
    sell" restates the offer's own service, not who it is for."""

    html = (
        "<html><head><title>T</title></head><body>"
        "<h1>Weekend catering for local events</h1>"
        "<p>We cook for busy parents.</p>"
        "<p>Offline: a real hour for the service you sell.</p>"
        "</body></html>"
    )

    offer = read_offer((page_material(SITE, html),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "the service you sell" not in audience_texts
    assert "busy parents" in audience_texts


def test_site_visitors_are_never_read_as_an_audience_claim() -> None:
    """The contract's own rule -- "Cold is people found in the open
    field, not visitors who already filled your form" -- means a phrase
    describing the reader's own site visitors can never be a real
    audience claim, even when it is grammatically "for X"."""

    html = (
        "<html><head><title>T</title></head><body>"
        "<h1>Weekend catering for local events</h1>"
        "<p>We cook for busy parents.</p>"
        "<p>The widget is a conversation channel for someone already on the site.</p>"
        "</body></html>"
    )

    offer = read_offer((page_material(SITE, html),))

    audience_texts = {claim.text.casefold() for claim in offer.who_may_fit}
    assert "someone already on the site" not in audience_texts
    assert "busy parents" in audience_texts


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
