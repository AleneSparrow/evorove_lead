"""Cycle 1 policy. Not SalesStage. Not ProcessState. Never send."""

from __future__ import annotations

from evorove_lead.offer import OfferUnderstanding


class LeadGenerationPolicy:
    """What the engine is allowed to do next."""

    def may_read_presence(self, site_url: str) -> bool:
        return bool((site_url or "").strip())

    def may_seek_people(
        self, *, offer: OfferUnderstanding | None, search_connected: bool
    ) -> bool:
        return offer is not None and search_connected
