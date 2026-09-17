from pathlib import Path

from evorove_lead.business import BusinessSeed
from evorove_lead.client_zero import (
    CLIENT_ZERO_SITE,
    OWNER_MATERIALS_DIR,
    _WithOwnerMaterials,
    client_zero_presence,
    client_zero_seed,
    search_summary,
)
from evorove_lead.engine import GenerationResult, GenerationStatus
from evorove_lead.materials import DepositedMaterial
from evorove_lead.web_people_search import hypothesis_search_from_env


class _FakePresence:
    def __init__(self, materials: tuple[DepositedMaterial, ...]) -> None:
        self._materials = materials

    def load(self, seed: BusinessSeed) -> tuple[DepositedMaterial, ...]:
        return self._materials


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


def test_owner_materials_are_deposited_ahead_of_the_live_site(tmp_path: Path) -> None:
    """A short, ordinary sentence the owner deposits should stand as the
    offer's leading claim -- the live SPA's own hero taglines and FAQ
    headings parse poorly, per docs/client-zero-pilot-checklist-ru.md."""

    (tmp_path / "service.txt").write_text(
        "Sales automation for small local service businesses.\n", encoding="utf-8"
    )
    live = _FakePresence((DepositedMaterial(name=CLIENT_ZERO_SITE, body="SHOUTING HERO LINE"),))
    presence = _WithOwnerMaterials(live, tmp_path)

    materials = presence.load(client_zero_seed())

    assert materials[0].name == "service.txt"
    assert materials[1].name == CLIENT_ZERO_SITE


def test_missing_owner_materials_directory_only_uses_the_live_site(tmp_path: Path) -> None:
    """Optional means optional: no directory, no owner-materials -- not an error."""

    live = _FakePresence((DepositedMaterial(name=CLIENT_ZERO_SITE, body="Weekend catering."),))
    presence = _WithOwnerMaterials(live, tmp_path / "does-not-exist")

    materials = presence.load(client_zero_seed())

    assert materials == (DepositedMaterial(name=CLIENT_ZERO_SITE, body="Weekend catering."),)


def test_client_zero_presence_points_at_the_repos_own_owner_materials_dir() -> None:
    assert OWNER_MATERIALS_DIR.name == "owner-materials"
    assert client_zero_presence() is not None


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
