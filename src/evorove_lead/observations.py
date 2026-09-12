"""Owner-deposited people observations. Not a web directory and not a send queue.

The owner copies a public post, a person they already know, or a fact they
can stand behind. This module does not fetch third-party people directories.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence

from evorove_lead.offer import OfferUnderstanding
from evorove_lead.search import PeopleHit

SKIP_NAMES = frozenset({"README.md", "README.txt", ".gitkeep"})
ALLOWED_SUFFIXES = frozenset({".jsonl"})
ALLOWED_CHANNELS = frozenset({"sms", "email"})


class ObservationRejected(ValueError):
    """A path is not an allowed owner-observation source."""


def _is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return lowered == ".env" or lowered.startswith(".env.")


def _optional_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_email(value: str) -> str:
    normalized = value.casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        return ""
    return normalized


def _normalize_phone(value: str) -> str:
    prefix = "+" if value.startswith("+") else ""
    digits = "".join(character for character in value if character.isdigit())
    if not 7 <= len(digits) <= 15:
        return ""
    return f"{prefix}{digits}"


def compose_observation_identity(
    *, name: str, email: str, phone: str, channel: str
) -> str:
    address = email if channel == "email" else phone
    if name:
        return f"{name}, {address}"
    return address


def observation_to_hit(record: object) -> PeopleHit | None:
    """Turn one deposited object into a hit, or drop a dump / incomplete row."""

    if not isinstance(record, dict):
        return None
    observed_fact = _optional_text(record.get("observed_fact"))
    observed_source = _optional_text(record.get("observed_source"))
    channel = _optional_text(record.get("channel")).casefold()
    name = _optional_text(record.get("name"))
    email = _normalize_email(_optional_text(record.get("email")))
    phone = _normalize_phone(_optional_text(record.get("phone")))
    if not observed_fact or not observed_source:
        return None
    if channel not in ALLOWED_CHANNELS:
        return None
    if channel == "email" and not email:
        return None
    if channel == "sms" and not phone:
        return None
    return PeopleHit(
        identity=compose_observation_identity(
            name=name, email=email, phone=phone, channel=channel
        ),
        observed_fact=observed_fact,
        observed_source=observed_source,
        channel=channel,
    )


def _hits_from_jsonl(path: Path) -> tuple[PeopleHit, ...]:
    hits: list[PeopleHit] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        hit = observation_to_hit(record)
        if hit is not None:
            hits.append(hit)
    return tuple(hits)


def load_owner_observations(root: Path) -> tuple[PeopleHit, ...]:
    """Load people the owner deposited. README, dumps, and secrets are not hits."""

    if not root.is_dir():
        raise ObservationRejected("owner-observations directory is required")

    hits: list[PeopleHit] = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if _is_secret_name(path.name):
            raise ObservationRejected("refusing to load secrets")
        if path.name in SKIP_NAMES or path.name.startswith("."):
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        hits.extend(_hits_from_jsonl(path))
    return tuple(hits)


class OwnerObservationPeopleSearch:
    """Connected source: people the owner deposited, each with an observed fact.

    This is not a live web directory, LinkedIn scrape, or ads OAuth client.
    Default engines still use UnconnectedPeopleSearch.
    """

    connected = True

    def __init__(self, root: Path) -> None:
        self._root = root

    def find(self, offer: OfferUnderstanding) -> Sequence[PeopleHit]:
        del offer
        return load_owner_observations(self._root)
