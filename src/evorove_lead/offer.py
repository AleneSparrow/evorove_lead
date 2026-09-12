"""Structured offer for cycle 1, grounded in owner-deposited materials.

A proposed understanding is accepted only if every claim already appears
in those files. This module does not call an LLM, invent a price, or
search for people.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from evorove_lead.materials import DepositedMaterial

COMMERCIAL_KINDS = ("price", "discount", "guarantee", "legal_claim")


class OfferRejected(ValueError):
    """A proposed offer is not grounded in owner materials."""


@dataclass(frozen=True)
class GroundedClaim:
    """A statement that must already appear in named business materials."""

    text: str
    source_name: str


@dataclass(frozen=True)
class CommercialClaim:
    """A commercial statement. Allowed only when the owner already wrote it."""

    kind: str
    text: str
    source_name: str


@dataclass(frozen=True)
class OfferUnderstanding:
    """What we sell, who may fit, and what we must not invent."""

    what_we_sell: tuple[GroundedClaim, ...]
    who_may_fit: tuple[GroundedClaim, ...]
    commercial_claims: tuple[CommercialClaim, ...]
    must_not_promise: tuple[str, ...]


def _index_materials(materials: Sequence[DepositedMaterial]) -> dict[str, DepositedMaterial]:
    return {item.name: item for item in materials}


def _require_text(field: str, value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise OfferRejected(f"{field} is required")
    return text


def _ground(claim_text: str, source_name: str, by_name: dict[str, DepositedMaterial]) -> None:
    text = _require_text("claim", claim_text)
    source = _require_text("source_name", source_name)
    material = by_name.get(source)
    if material is None:
        raise OfferRejected(f"source {source!r} is not a deposited material")
    if text.casefold() not in material.body.casefold():
        raise OfferRejected(f"claim is not in {source}")


def accept_offer_understanding(
    *,
    materials: Sequence[DepositedMaterial],
    what_we_sell: Iterable[GroundedClaim],
    who_may_fit: Iterable[GroundedClaim],
    commercial_claims: Iterable[CommercialClaim] = (),
) -> OfferUnderstanding:
    """Accept a structured offer or reject invented claims.

    Product invariant: no business materials, no offer. No quote in those
    words, no price, discount, guarantee, or legal claim.
    """

    if not materials:
        raise OfferRejected("business materials are required")

    by_name = _index_materials(materials)
    sold = tuple(what_we_sell)
    audience = tuple(who_may_fit)
    commercial = tuple(commercial_claims)

    if not sold:
        raise OfferRejected("what_we_sell is required")
    if not audience:
        raise OfferRejected("who_may_fit is required")

    for claim in sold:
        _ground(claim.text, claim.source_name, by_name)
    for claim in audience:
        _ground(claim.text, claim.source_name, by_name)

    grounded_kinds: set[str] = set()
    for claim in commercial:
        kind = _require_text("kind", claim.kind)
        if kind not in COMMERCIAL_KINDS:
            raise OfferRejected(f"unknown commercial kind {kind!r}")
        _ground(claim.text, claim.source_name, by_name)
        grounded_kinds.add(kind)

    return OfferUnderstanding(
        what_we_sell=sold,
        who_may_fit=audience,
        commercial_claims=commercial,
        must_not_promise=tuple(kind for kind in COMMERCIAL_KINDS if kind not in grounded_kinds),
    )
