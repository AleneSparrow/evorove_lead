"""Level B enrichment reads saved text and does not invent a mailbox."""
from evorove_lead.enrichment_queue import email_local_hint, enrich_company


HTML = """
<html><body>
<a href="/about">About</a>
<a href="/team">Team</a>
<a href="/contact">Contact</a>
<a href="/leadership">Leadership</a>
<a href="https://other.example/about">Elsewhere</a>
<script type="application/ld+json">{"@type":"Person","name":"Maria Alvarez","jobTitle":"Founder"}</script>
<form action="/contact"><input name="email"></form>
</body></html>
"""


def test_fetch_limit_blocks_and_hint_is_not_a_mail_path():
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/contact"):
            return {"status": "blocked_403"}
        return {"text": "Jane Doe is the managing director."}

    result = enrich_company(
        domain="northwind.example",
        pages=(("https://northwind.example/", HTML),),
        gaps=("named_decision_maker", "email_mx", "quote"),
        fetch_page=fetch,
        mx_check=lambda domain: domain == "northwind.example",
    )
    assert result.fetches == 3
    assert len(calls) == 3
    assert any(item.startswith("fetch_limit:") for item in result.skipped)
    assert any(item.startswith("blocked_403:") for item in result.skipped)
    assert calls.count("https://northwind.example/contact") == 1
    assert "https://other.example/about" not in calls
    assert result.person_name == "Maria Alvarez" and result.person_role == "Founder"
    assert result.unverified_email["verified"] is False
    assert result.unverified_email["mail_path"] is False
    assert result.unverified_email["mx"] is True
    assert result.unverified_email["value"] == "maria.alvarez@northwind.example"
    assert email_local_hint("Maria Alvarez", "northwind.example", mx_ok=False)["mail_path"] is False


def test_saved_json_ld_and_form_need_no_fetch():
    result = enrich_company(
        domain="northwind.example",
        pages=(("https://northwind.example/about", HTML),),
        gaps=("contact_form", "named_decision_maker"),
    )
    assert result.fetches == 0
    assert result.person_name == "Maria Alvarez"
    assert result.contact_form == "https://northwind.example/contact"
    assert "no_model" in result.skipped
    assert "named_decision_maker" not in result.missing
    assert "contact_form" not in result.missing
