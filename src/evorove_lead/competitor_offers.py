"""Drop companies whose own offer is the motion Evorove sells. No model."""
from __future__ import annotations

import json
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "competitor_offers.json"


def load_competitor_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    compiled = []
    for item in data["patterns"]:
        compiled.append((item["id"], re.compile(item["pattern"], re.I)))
    if not compiled:
        raise RuntimeError("competitor patterns are empty")
    return tuple(compiled)


def offer_surface(*, title: str = "", h1: str = "", description: str = "", navigation: str = "") -> str:
    """The main offer: title, h1, description, and the start of the menu.

    A long service menu is not the offer. One item buried in it does not
    make a marketing agency a lead-generation company.
    """
    nav = " ".join((navigation or "").split())[:100]
    parts = [" ".join(part.split()) for part in (title, h1, description, nav) if part and part.strip()]
    return " ".join(parts)


def competitor_offer(text: str, patterns=None) -> str:
    """Id of the offer this surface sells, or empty when it does not match."""
    body = text or ""
    for offer_id, pattern in patterns or load_competitor_patterns():
        if pattern.search(body):
            return offer_id
    return ""
