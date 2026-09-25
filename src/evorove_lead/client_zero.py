"""Client 0 is Evorove selling itself.

Search metrics start here. This module does not message anyone and does not
print a found person's email, phone, or reason. Counts only.
"""

from __future__ import annotations

import os
from pathlib import Path

from evorove_lead.business import BusinessSeed
from evorove_lead.engine import LeadGenerationEngine
from evorove_lead.materials import DepositedMaterial, load_deposited_materials
from evorove_lead.presence import HttpPresenceSource, PresenceSource
from evorove_lead.sqlalchemy_warehouse import flush_crm_deliveries_from_env
from evorove_lead.web_people_search import hypothesis_search_from_env

CLIENT_ZERO_SITE = "https://evorove.com"
CLIENT_ZERO_ARCHETYPE = "software subscription"
OWNER_MATERIALS_DIR = Path(__file__).resolve().parent.parent.parent / "owner-materials"


class _WithOwnerMaterials:
    """The live site, plus whatever the owner deposited in `owner-materials/`.

    The live page is real, but its own marketing style (hero taglines,
    FAQ headings) reads poorly for `offer_reader.py`'s plain-English
    heuristics. This never replaces the live fetch -- it only adds
    deposited files ahead of it, so a clean, ordinary sentence there can
    stand as the offer's `what_we_sell` (used first) while the live page
    still supplies its own audience mentions and price. An empty or
    missing directory changes nothing: `owner-materials/` already exists
    as an optional, git-tracked spot for this (see its own README).
    """

    def __init__(self, live: PresenceSource, materials_dir: Path) -> None:
        self._live = live
        self._materials_dir = materials_dir

    def load(self, seed: BusinessSeed) -> tuple[DepositedMaterial, ...]:
        deposited: tuple[DepositedMaterial, ...] = ()
        if self._materials_dir.is_dir():
            deposited = load_deposited_materials(self._materials_dir)
        return deposited + self._live.load(seed)


def client_zero_presence() -> PresenceSource:
    return _WithOwnerMaterials(HttpPresenceSource(), OWNER_MATERIALS_DIR)


def client_zero_seed() -> BusinessSeed:
    """CRM tenant id is optional until the boards are glued. The site is not."""

    business_id = (os.getenv("EVOROVE_CLIENT_ZERO_BUSINESS_ID") or "").strip()
    return BusinessSeed(
        site_url=CLIENT_ZERO_SITE,
        business_id=business_id,
        business_archetype=CLIENT_ZERO_ARCHETYPE,
    )


def search_summary(result) -> dict[str, int | str]:
    """Counts the owner can read. Never a contact."""

    return {
        "status": result.status.value,
        "candidates": len(result.candidates),
        "handoffs": len(result.handoffs),
        "rejected": len(result.rejected),
        "messages_sent": 0,
    }


def run_client_zero_search() -> dict[str, int | str]:
    search = hypothesis_search_from_env()
    if search is None:
        summary: dict[str, int | str] = {
            "status": "search_unconnected",
            "candidates": 0,
            "handoffs": 0,
            "rejected": 0,
            "messages_sent": 0,
        }
    else:
        engine = LeadGenerationEngine(
            presence=client_zero_presence(),
            hypothesis_search=search,
            # Client 0 is a nationwide US SaaS product, not a local service
            # business with one city or service area -- there is no real
            # locality to infer here, only this product's own jargon
            # ("CRM", "Cold") that a text heuristic can mistake for one (real
            # bug found piloting this: "they land in CRM on Cold" read as a
            # place). The honest geo_radius for client 0 is the whole US
            # market, not a guess -- leave inference off (default `False`).
        )
        result = engine.generate(client_zero_seed())
        summary = search_summary(result)
        if result.sent_messages:
            raise RuntimeError("cycle 1 must not send")
    delivery = flush_crm_deliveries_from_env()
    summary["crm_redelivered"] = delivery["redelivered"]
    summary["crm_pending"] = delivery["pending"]
    return summary


def main() -> None:
    summary = run_client_zero_search()
    print(
        "client_zero status={status} candidates={candidates} "
        "handoffs={handoffs} rejected={rejected} messages_sent={messages_sent} "
        "crm_pending={crm_pending} crm_redelivered={crm_redelivered}".format(
            **summary
        )
    )
    if summary["status"] == "search_unconnected":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
