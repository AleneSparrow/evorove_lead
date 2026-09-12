"""The business cycle 1 looks at. Not a pasted essay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessSeed:
    """Pointer to the owner's business presence.

    A public site URL is enough. The owner does not have to paste ad copy.
    `business_id` is the CRM tenant id when the engines are glued.

    `business_archetype` is opaque, owner-set data -- e.g. "local service
    appointment", "professional service" -- not a code-level industry
    branch (AGENTS.md: no industry forks in code). It only ever feeds the
    system-wide, non-tenant pattern library (phase 4): which channel/
    intent-trigger shapes closed well for other businesses of the same
    self-described kind. Blank means "no pattern-library lookup yet."
    """

    site_url: str
    business_id: str = ""
    business_archetype: str = ""
