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
