"""The business cycle 1 looks at. Not a pasted essay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessSeed:
    """Pointer to the owner's business presence.

    A public site URL is enough. The owner does not have to paste ad copy.
    `business_id` is the CRM tenant id when the engines are glued.
    """

    site_url: str
    business_id: str = ""
