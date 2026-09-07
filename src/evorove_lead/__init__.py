"""Cycle 1 domain: grounded offer, then a person plus a reason.

This package does not send messages or book time. People search is a
port; the default source is unconnected.
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
from evorove_lead.materials import DepositedMaterial, MaterialRejected, load_deposited_materials
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
from evorove_lead.search import PeopleHit, UnconnectedPeopleSearch

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
    "GroundedClaim",
    "HttpPresenceSource",
    "LeadGenerationEngine",
    "LeadGenerationPolicy",
    "MaterialRejected",
    "OfferRejected",
    "OfferUnderstanding",
    "PeopleHit",
    "PresenceRejected",
    "RejectedHit",
    "UnconnectedPeopleSearch",
    "accept_candidate",
    "accept_candidate_for_offer",
    "accept_offer_understanding",
    "load_deposited_materials",
    "page_material",
    "read_offer",
]
