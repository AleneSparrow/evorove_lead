"""Drop search hits that are not a company's own site. The list is data, not code."""
from __future__ import annotations

import json
from pathlib import Path

from evorove_lead.platforms import registrable_domain

_PATH = Path(__file__).resolve().parent / "data" / "domain_blocklist.json"
_REASONS = (
    "job_board", "social", "encyclopedia", "dictionary", "qa_forum",
    "list_catalog", "marketplace", "large_corporation",
)


def load_blocklist() -> dict[str, tuple[str, ...]]:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    loaded = {}
    for reason in _REASONS:
        hosts = data.get(reason)
        if not isinstance(hosts, list) or not hosts:
            raise RuntimeError(f"blocklist missing {reason}")
        loaded[reason] = tuple(item.casefold().strip(".") for item in hosts if isinstance(item, str) and item.strip())
    return loaded


def block_reason(host: str, listing: dict | None = None) -> str:
    """Why this host is not a company site. Empty when it is not on the list."""
    host = (host or "").casefold().strip(".")
    if not host:
        return ""
    domain = registrable_domain(host)
    groups = listing if listing is not None else load_blocklist()
    for reason in _REASONS:
        for item in groups.get(reason, ()):
            if host == item or host.endswith("." + item) or domain == item:
                return reason
    return ""
