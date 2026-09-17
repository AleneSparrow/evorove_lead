from evorove_lead.client_zero import CLIENT_ZERO_SITE, client_zero_seed, search_summary
from evorove_lead.engine import GenerationResult, GenerationStatus
from evorove_lead.web_people_search import hypothesis_search_from_env


def test_client_zero_is_evorove_itself() -> None:
    seed = client_zero_seed()
    assert seed.site_url == CLIENT_ZERO_SITE == "https://evorove.com"
    assert seed.business_archetype == "software subscription"


def test_live_search_stays_off_without_a_search_url(monkeypatch) -> None:
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    assert hypothesis_search_from_env() is None


def test_live_search_turns_on_when_the_owner_points_at_searx(monkeypatch) -> None:
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "http://localhost:8080")
    search = hypothesis_search_from_env()
    assert search is not None
    assert search.connected is True


def test_search_summary_never_includes_a_contact() -> None:
    result = GenerationResult(
        status=GenerationStatus.PEOPLE_FOUND,
        offer=None,
        candidates=(),
        handoffs=(),
        rejected=(),
    )
    summary = search_summary(result)
    blob = " ".join(str(value) for value in summary.values())
    assert "@" not in blob
    assert summary["messages_sent"] == 0
