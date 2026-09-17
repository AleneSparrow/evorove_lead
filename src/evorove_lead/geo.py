"""Infer a pilot GeoRadius from the brief's own words. Grounded only.

Phase 2's contract note: "geo_radius по умолчанию — город/зона клиента
из брифа, не весь рынок США." This is that inference -- opt-in, not the
engine's silent default (see `LeadGenerationEngine`'s
`infer_geo_radius_from_brief` flag): a locality heuristic that scans
free text can misfire, and narrowing production search scope by surprise
is worse than the honest, wide default. Never invents a city; only
returns one that literally appears in the materials.
"""

from __future__ import annotations

import re
from typing import Sequence

from evorove_lead.hypothesis import GeoRadius
from evorove_lead.materials import DepositedMaterial

# Capitalized so "in town", "in fact", "in 2020" (lowercase or digits)
# don't match -- a real city name is written capitalized. The negative
# lookbehind for "sign "/"log " excludes "Sign in", "Log in" -- a nav-bar
# link, not a locality preposition. Real bug found piloting evorove.com:
# its header text has no separator between adjacent nav links ("...FAQ
# Sign in Start free trial..."), so "in" (from "Sign in") followed
# straight into "Start" (from the "Start free trial" button) read as
# "in Start" -- a fabricated city that never existed on the page as a
# sentence.
_LOCALITY_RE = re.compile(
    r"\b(?<![Ss]ign )(?<![Ll]og )(?:[Ii]n|[Nn]ear|[Ss]erving|[Bb]ased in|[Ll]ocated in)\s+"
    r"([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2})"
    r"(?:,\s*([A-Z]{2}))?\b"
)


def infer_geo_radius(materials: Sequence[DepositedMaterial]) -> GeoRadius:
    """The first capitalized "in/near/serving/based in/located in <Place>" hit.

    Whole-US default (`GeoRadius()`) if nothing grounded turns up --
    never a guess, never an empty-string locality standing in for one.
    """

    for material in materials:
        match = _LOCALITY_RE.search(material.body)
        if match:
            locality = match.group(1).strip()
            state = match.group(2)
            return GeoRadius(locality=f"{locality}, {state}" if state else locality)
    return GeoRadius()
