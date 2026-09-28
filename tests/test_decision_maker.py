"""Module 3: a named owner/founder/director, not the first address on the page."""

from evorove_lead.decision_maker import find_decision_maker
from evorove_lead.presence import PresenceRejected


def _fetcher(pages: dict[str, str]):
    def fetch(url: str) -> str:
        if url not in pages:
            raise PresenceRejected("no such page")
        return pages[url]

    return fetch


def test_finds_a_named_founder_and_their_own_email_on_the_homepage():
    pages = {
        "https://www.tonys-pizza.com/": (
            "Tony's Pizza, Austin. Founded by Jane Doe, Owner and Founder. "
            "Email Jane at jane@tonys-pizza.com or the front desk at info@tonys-pizza.com."
        ),
    }
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher(pages))

    assert result is not None
    assert result.name == "Jane Doe"
    assert result.email == "jane@tonys-pizza.com"
    assert result.source_url == "https://www.tonys-pizza.com/"
    assert "Jane Doe" in result.evidence_quote


def test_checks_a_guessed_about_page_when_the_homepage_has_nothing():
    pages = {
        "https://www.tonys-pizza.com/": "Tony's Pizza. Family owned since 1994.",
        "https://www.tonys-pizza.com/about": (
            "Meet our founder, John Smith. Reach him at john@tonys-pizza.com."
        ),
    }
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher(pages))

    assert result is not None
    assert result.name == "John Smith"
    assert result.email == "john@tonys-pizza.com"
    assert result.source_url == "https://www.tonys-pizza.com/about"


def test_falls_back_to_the_only_non_generic_email_on_the_same_page():
    pages = {
        "https://www.tonys-pizza.com/": (
            "Maria Alvarez, our Director, leads the kitchen. Reach the shop at kitchen@tonys-pizza.com."
        ),
    }
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher(pages))

    assert result is not None
    assert result.name == "Maria Alvarez"
    assert result.email == "kitchen@tonys-pizza.com"


def test_never_returns_a_generic_business_inbox_as_the_matched_email():
    pages = {
        "https://www.tonys-pizza.com/": (
            "Our Owner, Maria Alvarez, started the shop. "
            "Only public address: info@tonys-pizza.com."
        ),
    }
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher(pages))

    assert result is None


def test_skips_a_leading_determiner_or_the_role_word_itself_as_a_false_name():
    pages = {
        "https://www.tonys-pizza.com/": (
            "Our Director Maria Alvarez leads the kitchen. Reach her at maria@tonys-pizza.com."
        ),
    }
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher(pages))

    assert result is not None
    assert result.name == "Maria Alvarez"
    assert result.email == "maria@tonys-pizza.com"


def test_returns_none_when_no_page_names_a_decision_maker():
    pages = {
        "https://www.tonys-pizza.com/": "Tony's Pizza. Open daily. Call (415) 555-0134.",
    }
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher(pages))

    assert result is None


def test_returns_none_when_every_page_fails_to_fetch():
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher({}))

    assert result is None


def test_ignores_an_email_on_a_different_domain():
    pages = {
        "https://www.tonys-pizza.com/": (
            "Our Founder, Jane Doe, is quoted in the press. Media contact: jane@example-press.com."
        ),
    }
    result = find_decision_maker("https://www.tonys-pizza.com/", "tonys-pizza.com", _fetcher(pages))

    assert result is None
