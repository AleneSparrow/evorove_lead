"""Cycle 1 domain: grounded offer, then a person plus a reason.

This package does not send messages or book time. People search is a
port; the default source is unconnected. Owner-deposited observations
are a connected source without a live web directory.
"""

from evorove_lead.business import BusinessSeed
from evorove_lead.candidate import (
    Candidate,
    CandidateRejected,
    accept_candidate,
    accept_candidate_for_offer,
)
from evorove_lead.engine import (
    GenerationResult,
    GenerationStatus,
    LeadGenerationEngine,
    RejectedHit,
)
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.hypothesis import (
    GeoRadius,
    Hypothesis,
    HypothesisProbe,
    IntentTrigger,
    build_hypotheses,
    prioritize_hypotheses,
    verify_hypothesis,
)
from evorove_lead.materials import DepositedMaterial, MaterialRejected, load_deposited_materials
from evorove_lead.observations import (
    ObservationRejected,
    OwnerObservationPeopleSearch,
    load_owner_observations,
)
from evorove_lead.offer import (
    COMMERCIAL_KINDS,
    CommercialClaim,
    GroundedClaim,
    OfferRejected,
    OfferUnderstanding,
    accept_offer_understanding,
)
from evorove_lead.offer_reader import read_offer
from evorove_lead.policy import LeadGenerationPolicy
from evorove_lead.presence import HttpPresenceSource, PresenceRejected, page_material
from evorove_lead.search import (
    HypothesisPeopleSearch,
    PeopleHit,
    TraceFinding,
    UnconnectedPeopleSearch,
)
from evorove_lead.web_people_search import WebSearchPeopleSearch
from evorove_lead.web_search import (
    HttpSearxngWebSearchClient,
    SearchHit,
    WebSearchClient,
    client_from_env as web_search_client_from_env,
    fetch_page_text,
)

__all__ = [
    "COMMERCIAL_KINDS",
    "BusinessSeed",
    "Candidate",
    "CandidateRejected",
    "CommercialClaim",
    "Cycle1Handoff",
    "DepositedMaterial",
    "GenerationResult",
    "GenerationStatus",
    "GeoRadius",
    "GroundedClaim",
    "HttpPresenceSource",
    "HttpSearxngWebSearchClient",
    "Hypothesis",
    "HypothesisPeopleSearch",
    "HypothesisProbe",
    "IntentTrigger",
    "LeadGenerationEngine",
    "LeadGenerationPolicy",
    "MaterialRejected",
    "ObservationRejected",
    "OfferRejected",
    "OfferUnderstanding",
    "OwnerObservationPeopleSearch",
    "PeopleHit",
    "PresenceRejected",
    "RejectedHit",
    "SearchHit",
    "TraceFinding",
    "UnconnectedPeopleSearch",
    "WebSearchClient",
    "WebSearchPeopleSearch",
    "accept_candidate",
    "accept_candidate_for_offer",
    "accept_offer_understanding",
    "build_hypotheses",
    "fetch_page_text",
    "load_deposited_materials",
    "load_owner_observations",
    "page_material",
    "prioritize_hypotheses",
    "read_offer",
    "verify_hypothesis",
    "web_search_client_from_env",
]
