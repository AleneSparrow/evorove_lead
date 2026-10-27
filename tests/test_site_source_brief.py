"""A public owner site is enough to form a source-search brief."""

import json
from pathlib import Path

from evorove_lead import client_zero
from evorove_lead.business import BusinessSeed
from evorove_lead.marketing_analysis import MarketingAnalyzer, ScriptedMarketingCompletion
from evorove_lead.presence import page_material


FIXTURE = Path(__file__).parent / "fixtures" / "evorove_landing.html"
SITE = "https://evorove.com"


class Presence:
    def __init__(self, url: str, html: str):
        self.material = page_material(url, html)

    def load(self, seed: BusinessSeed):
        return (self.material,)


def _fact(quote: str, source: str):
    return {"quote": quote, "source_name": source}


def _segment(label: str, quote: str, source: str):
    return {"label": label, "evidence_quote": quote, "source_name": source,
            "channel": "business_listing"}


def test_evorove_site_yields_a_brief_without_service_file(monkeypatch, tmp_path):
    monkeypatch.setattr(client_zero, "OWNER_MATERIALS_DIR", tmp_path)
    html = FIXTURE.read_text(encoding="utf-8")
    product = "It studies your offer, looks at public data, and checks each person twice against what you described."
    result = {"product": _fact(product, SITE), "price": None,
              "place": _fact("The United States today.", SITE),
              "promotion": None, "segments": [
                  _segment("businesses seeking customer acquisition and sales", "finds people in the open web", SITE),
                  _segment("business owners seeking an automated sales process", "Each Cold lead gets a live conversation", SITE),
              ]}
    analyzer = MarketingAnalyzer(ScriptedMarketingCompletion(json.dumps(result)))
    brief = client_zero.client_zero_source_analysis("client-zero", presence=Presence(SITE, html),
                                                    marketing_analyzer=analyzer)
    assert "public data" in brief.services[0]
    assert all("business" in segment for segment in brief.icp_segments)
    assert brief.geography == "United States"
    assert "The United States today." in brief.brief
    assert "COLD IN" not in brief.services[0]
    assert "What does Done mean" not in brief.services[0]
    assert "US B2B businesses with high-value deals" not in brief.blob()


def test_structurally_different_local_business_uses_its_own_offer_and_geo(tmp_path, monkeypatch):
    monkeypatch.setattr(client_zero, "OWNER_MATERIALS_DIR", tmp_path)
    site = "https://bakery.example"
    html = """<html><head><title>Oak Bakery</title></head><body>
    <h1>Fresh bread for Austin neighbors</h1>
    <p>We bake sourdough bread and custom cakes for local events.</p>
    <p>Our shop serves Austin, Texas.</p>
    <p>We work with families planning celebrations and offices ordering team lunches.</p>
    </body></html>"""
    result = {"product": _fact("We bake sourdough bread and custom cakes for local events.", site),
              "price": None, "place": _fact("serves Austin, Texas", site), "promotion": None,
              "segments": [
                  _segment("families planning celebrations", "families planning celebrations", site),
                  _segment("offices ordering team lunches", "offices ordering team lunches", site),
              ]}
    analyzer = MarketingAnalyzer(ScriptedMarketingCompletion(json.dumps(result)))
    brief = client_zero.client_zero_source_analysis("bakery", presence=Presence(site, html),
                                                    marketing_analyzer=analyzer)
    assert "sourdough" in brief.services[0]
    assert brief.geography == "Austin, Texas, United States"
    assert brief.localities == ("Austin, Texas",)
    assert brief.customer_categories == ("families planning celebrations", "offices ordering team lunches")


def test_service_file_adds_context_but_is_not_required(tmp_path, monkeypatch):
    monkeypatch.setattr(client_zero, "OWNER_MATERIALS_DIR", tmp_path)
    (tmp_path / "service.txt").write_text(
        "Evorove sells a subscription that handles sales conversations for business owners.",
        encoding="utf-8",
    )
    site = Presence(SITE, FIXTURE.read_text(encoding="utf-8"))
    presence = client_zero._WithOwnerMaterials(site, tmp_path)
    response = {"product": _fact("a subscription that handles sales conversations", "service.txt"),
                "price": None, "place": None, "promotion": None, "segments": [
                    _segment("business owners", "for business owners", "service.txt"),
                    _segment("owners with active lead conversations", "Each Cold lead gets a live conversation", SITE),
                ]}

    class RecordingCompletion:
        user = ""

        def complete(self, *, system, user):
            self.user = user
            return json.dumps(response)

    completion = RecordingCompletion()
    brief = client_zero.client_zero_source_analysis(
        "client-zero", presence=presence, marketing_analyzer=MarketingAnalyzer(completion))
    assert "sales conversations" in brief.services[0]
    assert "SOURCE NAME: service.txt" in completion.user
    assert f"SOURCE NAME: {SITE}" in completion.user


def test_slogan_and_question_cannot_supply_product_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(client_zero, "OWNER_MATERIALS_DIR", tmp_path)
    html = FIXTURE.read_text(encoding="utf-8")
    for slogan in ("COLD IN. DONE ON THE BOARD.", "What does Done mean for an offline vs online business?"):
        result = {"product": _fact(slogan, SITE), "price": None, "place": None,
                  "promotion": None, "segments": [
                      _segment("business owners", "Each Cold lead gets a live conversation", SITE),
                      _segment("businesses", "It studies your offer", SITE),
                  ]}
        analyzer = MarketingAnalyzer(ScriptedMarketingCompletion(json.dumps(result)))
        try:
            client_zero.client_zero_source_analysis("client-zero", presence=Presence(SITE, html),
                                                    marketing_analyzer=analyzer)
        except ValueError:
            pass
        else:
            raise AssertionError("marketing slogan or FAQ question was accepted as the offer")


def test_board_stage_and_crm_are_not_inferred_as_geography(tmp_path, monkeypatch):
    monkeypatch.setattr(client_zero, "OWNER_MATERIALS_DIR", tmp_path)
    site = "https://sales-board.example"
    for term in ("Cold", "CRM"):
        html = f"""<html><body><p>We manage sales conversations for business owners.</p>
        <p>Business owners see every move in the board.</p>
        <p>Prospects land in {term} when the research is complete.</p></body></html>"""
        result = {"product": _fact("We manage sales conversations for business owners.", site),
                  "price": None, "place": _fact(f"land in {term}", site), "promotion": None,
                  "segments": [
                      _segment("business owners", "business owners", site),
                      _segment("owners managing prospects", "Prospects land", site),
                  ]}
        analyzer = MarketingAnalyzer(ScriptedMarketingCompletion(json.dumps(result)))
        brief = client_zero.client_zero_source_analysis("board", presence=Presence(site, html),
                                                        marketing_analyzer=analyzer)
        assert brief.localities == ()
        assert brief.geography == "United States"
