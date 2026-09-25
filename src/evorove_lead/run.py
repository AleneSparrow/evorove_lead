"""Run cycle 1 once for one business: brief -> people -> CRM Cold.

    python -m evorove_lead.run --business-id acme --site-url https://acme.example

`--materials DIR` reads the brief from owner-deposited files instead of
fetching the site (the site URL is still required as the brief's pointer).
`--observations DIR` uses owner-deposited JSONL as the people source -- a stub,
not the open web. Without it the default people source stays unconnected and
the run stops after understanding the offer.

CRM handoff and the warehouse come from the environment exactly as in
`LeadGenerationEngine` (`CRM_BASE_URL` + `INTERNAL_TASK_SECRET`, `DATABASE_URL`).
Nothing here sends a message to a person.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from evorove_lead.business import BusinessSeed
from evorove_lead.engine import GenerationResult, LeadGenerationEngine
from evorove_lead.materials import DepositedMaterial, load_deposited_materials
from evorove_lead.observations import OwnerObservationPeopleSearch
from evorove_lead.presence import HttpPresenceSource
from evorove_lead.sqlalchemy_warehouse import flush_crm_deliveries_from_env


class DepositedPresenceSource:
    """The brief from files the owner deposited, not a fetch of her site."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def load(self, seed: BusinessSeed) -> tuple[DepositedMaterial, ...]:
        return load_deposited_materials(self._root)


def build_engine(materials: Path | None, observations: Path | None) -> LeadGenerationEngine:
    presence = DepositedPresenceSource(materials) if materials else HttpPresenceSource()
    people = OwnerObservationPeopleSearch(observations) if observations else None
    return LeadGenerationEngine(presence=presence, people_search=people)


def summarize(result: GenerationResult) -> dict[str, object]:
    return {
        "status": result.status.value,
        "offer_understood": result.offer is not None,
        "cold": len(result.handoffs),
        "rejected": [{"identity": hit.identity, "why": hit.why} for hit in result.rejected],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evorove_lead.run", description=__doc__.splitlines()[0])
    parser.add_argument("--business-id", required=True, help="CRM tenant id the Cold people land on")
    parser.add_argument("--site-url", required=True, help="the business's public site: the brief, not a lead list")
    parser.add_argument("--business-archetype", default="", help="owner-set, opaque; feeds the pattern library")
    parser.add_argument("--materials", type=Path, help="read the brief from these deposited files")
    parser.add_argument("--observations", type=Path, help="owner-deposited JSONL people (stub source)")
    args = parser.parse_args(argv)

    engine = build_engine(args.materials, args.observations)
    result = engine.generate(
        BusinessSeed(
            site_url=args.site_url,
            business_id=args.business_id,
            business_archetype=args.business_archetype,
        )
    )
    summary = summarize(result)
    delivery = flush_crm_deliveries_from_env()
    summary["crm_redelivered"] = delivery["redelivered"]
    summary["crm_pending"] = delivery["pending"]
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
