"""Enrichment for a level-B company. Saved text first. No guessed mailbox.

Order is data: linked about/team/contact/leadership/careers pages, then JSON-LD,
then one model read of saved text, then forms. A page fetch stops on a block
and is not retried. The model step does nothing unless a caller passes an
extractor. An email local-part plus a domain MX check is an unverified hint,
not a contact path.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from evorove_lead.decision_maker import find_named_leader
from evorove_lead.platforms import registrable_domain
from evorove_lead.qualification_score import load_score_model

_HREF = re.compile(r"""href=["']([^"'#]+)["']""", re.I)
_JSON_LD = re.compile(
    r"""<script[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.I | re.S,
)
_FORM = re.compile(
    r"contact_form (https?://\S+) fields ([A-Za-z0-9_|. -]+)|<form[^>]*action=[\"']([^\"']*)[\"'][^>]*>",
    re.I,
)
_BLOCKED = frozenset({"blocked_403", "blocked_challenge", "blocked_connection", "blocked_no_text", "blocked_fetch", "robots_disallow"})


@dataclass
class EnrichmentResult:
    added: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    fetches: int = 0
    skipped: tuple[str, ...] = ()
    person_name: str = ""
    person_role: str = ""
    contact_form: str = ""
    quote: str = ""
    unverified_email: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "added": list(self.added),
            "missing": list(self.missing),
            "fetches": self.fetches,
            "skipped": list(self.skipped),
            "person_name": self.person_name,
            "person_role": self.person_role,
            "contact_form": self.contact_form,
            "quote": self.quote,
            "unverified_email": dict(self.unverified_email),
        }


def email_local_hint(name: str, domain: str, *, mx_ok: bool) -> dict:
    """A pattern such as jane.doe@domain. Never a mail path."""
    parts = [re.sub(r"[^a-z]", "", word.casefold()) for word in (name or "").split()]
    parts = [part for part in parts if part]
    if len(parts) < 2 or not domain:
        return {}
    local = f"{parts[0]}.{parts[-1]}"
    return {
        "value": f"{local}@{domain}",
        "mx": bool(mx_ok),
        "verified": False,
        "mail_path": False,
    }


def enrich_company(*, domain: str, pages: tuple, gaps: tuple[str, ...] = (),
                    fetch_page=None, extractor=None, mx_check=None,
                    model: dict | None = None) -> EnrichmentResult:
    """Read saved pages, then at most N new linked pages. `pages` are (url, text)."""
    data = model or load_score_model()
    policy = data["enrichment"]
    slugs = tuple(policy["page_slugs"])
    limit = int(policy["max_page_fetches"])
    stored = [(item[0], item[1] if len(item) > 1 else "") for item in pages]
    added: list[str] = []
    skipped: list[str] = []
    fetches = 0
    person_name, person_role, form, quote = "", "", "", ""

    known = {urlsplit(url).path.rstrip("/") or "/" for url, _text in stored}
    queued = _linked_targets(stored, domain, slugs, known)
    if fetch_page is None:
        if queued:
            skipped.append("linked_pages_not_fetched")
    else:
        for url in queued:
            if fetches >= limit:
                skipped.append(f"fetch_limit:{url}")
                continue
            outcome = fetch_page(url)
            fetches += 1
            status = (outcome or {}).get("status") or ""
            if status in _BLOCKED or not (outcome or {}).get("text"):
                skipped.append(f"{status or 'blocked_fetch'}:{url}")
                continue
            stored.append((url, outcome["text"]))
            added.append(f"page:{url}")

    person_name, person_role = _person_from_saved(stored)
    if person_name:
        added.append(f"person:{person_name}")

    if extractor is not None:
        blob = "\n".join(text for _url, text in stored if text)
        extracted = extractor.extract(domain, blob)
        if extracted.get("called") and extracted.get("quote"):
            quote = extracted["quote"]
            added.append("quote")
        if extracted.get("name") and not person_name:
            person_name = extracted["name"]
            person_role = extracted.get("role") or person_role
            added.append(f"person:{person_name}")
        if not extracted.get("called"):
            skipped.append(extracted.get("reason") or "model_skipped")
    else:
        skipped.append("no_model")

    form = _form_target(stored)
    if form:
        added.append(f"form:{form}")

    hint = {}
    if person_name and "email_mx" in gaps:
        mx_ok = bool(mx_check(domain)) if mx_check is not None else False
        hint = email_local_hint(person_name, domain, mx_ok=mx_ok)
        if hint:
            added.append("unverified_email_hint")

    still_missing = [gap for gap in gaps if not _gap_filled(gap, person_name, person_role, form, quote)]
    return EnrichmentResult(
        added=tuple(added), missing=tuple(still_missing), fetches=fetches,
        skipped=tuple(skipped), person_name=person_name, person_role=person_role,
        contact_form=form, quote=quote, unverified_email=hint,
    )


def _gap_filled(gap: str, person_name: str, person_role: str, form: str, quote: str) -> bool:
    if gap == "named_decision_maker":
        return bool(person_name and person_role)
    if gap == "contact_form":
        return bool(form)
    if gap == "quote":
        return bool(quote)
    return False


def _linked_targets(pages, domain: str, slugs: tuple[str, ...], known: set[str]) -> tuple[str, ...]:
    host = registrable_domain(domain)
    found = []
    for url, text in pages:
        base = url if "://" in url else f"https://{domain}/"
        for match in _HREF.finditer(text or ""):
            absolute = urljoin(base, match.group(1).strip())
            parsed = urlsplit(absolute)
            if registrable_domain(parsed.hostname or "") != host:
                continue
            path = parsed.path.rstrip("/") or "/"
            leaf = path.rsplit("/", 1)[-1]
            if leaf not in slugs or path in known or absolute in found:
                continue
            found.append(absolute)
            known.add(path)
    return tuple(found)


def _person_from_saved(pages) -> tuple[str, str]:
    people = []
    for url, text in pages:
        for block in _JSON_LD.findall(text or ""):
            people.extend(_people_in_json(block))
    found = find_named_leader(tuple((url, text, people) for url, text in pages)) if pages else None
    if found is None and people:
        found = find_named_leader((("https://saved.example/about", "", people),))
    if found is None:
        return "", ""
    return found[0], found[1]


def _people_in_json(block: str) -> list[tuple[str, str]]:
    try:
        payload = json.loads(block)
    except json.JSONDecodeError:
        return []
    nodes = payload if isinstance(payload, list) else [payload]
    found = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get("@type") or ""
        kinds = kind if isinstance(kind, list) else [kind]
        if "Person" not in kinds:
            continue
        name = str(node.get("name") or "").strip()
        role = str(node.get("jobTitle") or "").strip()
        if name:
            found.append((name, role))
    return found


def _form_target(pages) -> str:
    for url, text in pages:
        match = _FORM.search(text or "")
        if not match:
            continue
        if match.group(1):
            return match.group(1)
        action = match.group(3) or ""
        if action and "search" not in action.casefold():
            return urljoin(url, action)
    return ""
