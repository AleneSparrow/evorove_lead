"""Client 0 is Evorove selling itself.

Search metrics start here. This module does not message anyone and does not
print a found person's email, phone, or reason. Counts only.
"""

from __future__ import annotations

import os

from evorove_lead.business import BusinessSeed
from evorove_lead.engine import LeadGenerationEngine
from evorove_lead.presence import HttpPresenceSource
from evorove_lead.web_people_search import hypothesis_search_from_env

CLIENT_ZERO_SITE = "https://evorove.com"
CLIENT_ZERO_ARCHETYPE = "software subscription"


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
        return {
            "status": "search_unconnected",
            "candidates": 0,
            "handoffs": 0,
            "rejected": 0,
            "messages_sent": 0,
        }
    engine = LeadGenerationEngine(
        presence=HttpPresenceSource(),
        hypothesis_search=search,
        infer_geo_radius_from_brief=True,
    )
    result = engine.generate(client_zero_seed())
    summary = search_summary(result)
    if result.sent_messages:
        raise RuntimeError("cycle 1 must not send")
    return summary


def main() -> None:
    summary = run_client_zero_search()
    print(
        "client_zero status={status} candidates={candidates} "
        "handoffs={handoffs} rejected={rejected} messages_sent={messages_sent}".format(
            **summary
        )
    )
    if summary["status"] == "search_unconnected":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
