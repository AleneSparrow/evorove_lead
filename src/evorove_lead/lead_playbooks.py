"""Playbooks describe a client's leads. They do not describe the client's own industry.

Adding a client is a new JSON file in `playbooks/`. The loader refuses a file
that says it describes the client's industry, a source id outside the catalog,
or a collect flag on a disabled source.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from urllib.parse import urlsplit

from evorove_lead.company_facts import looks_english
from evorove_lead.domain_blocklist import block_reason
from evorove_lead.evidence import digest
from evorove_lead.platforms import is_platform, registrable_domain
from evorove_lead.source_catalog import CATALOG, source_by_id
from evorove_lead.source_planner import PlannedSource, SourcePlan

_DIR = Path(__file__).resolve().parent / "playbooks"
_TEMPERATURES = {"hot", "warm", "cold"}
_USES = {"collect", "skip", "unavailable"}
_FROM_PRICE = re.compile(r"\b(?:from|starting at|starts at)\s*\$\s*([0-9][\d,]*(?:\.\d+)?)", re.I)
_PER_MONTH = re.compile(r"^\s*(?:/|per)\s*(?:mo(?:nth)?)\b", re.I)
_TEAM_OF = re.compile(r"\bteam of\s+([2-9]|\d{2,})\b", re.I)
_TEAM_WORDS = re.compile(r"\b(?:our team|leadership team)\b", re.I)
_ROLES = re.compile(r"\b(?:founder|owner|ceo|sales|engineer|consultant|designer|recruiter|director)\b", re.I)
_QUERY_TOKEN = re.compile(r"\S+")
_QUERY_FORBIDDEN = frozenset({"hiring", "jobs", "job", "careers", "career"})


@dataclass(frozen=True)
class LeadPlaybook:
    id: str
    title: str
    version: str
    describes: str
    lead_summary: str
    client_note: str
    event_days: int
    context_days: int
    minimum_usd_per_year: int
    subsegments: tuple[dict, ...]
    signals: tuple[dict, ...]
    deal_signs: tuple[dict, ...]
    deal_how: str
    exclusions: tuple[dict, ...]
    sources: tuple[dict, ...]
    gaps: tuple[dict, ...]
    touch_angles: tuple[str, ...]

    def queries(self) -> tuple[str, ...]:
        found = []
        for sub in self.subsegments:
            if sub.get("status") == "needs_research":
                continue
            found.extend(sub["queries"])
        return tuple(found)

    def source_note(self, source_id: str) -> str:
        for item in self.sources:
            if item["id"] == source_id:
                return item["note"]
        return ""

    def menu(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "describes": self.describes,
            "lead_summary": self.lead_summary,
            "subsegments": [{"id": item["id"], "title": item["title"]} for item in self.subsegments],
            "queries": len(self.queries()),
            "sources_collected": [item["id"] for item in self.sources if item["use"] == "collect"],
            "gaps": [dict(item) for item in self.gaps],
        }


@dataclass(frozen=True)
class PlaybookDecision:
    rejection: str = ""
    kind: str = ""
    temperature: str = ""
    segment: str = ""
    reason: str = ""
    quotes: tuple[str, ...] = ()


def load_playbooks() -> tuple[LeadPlaybook, ...]:
    paths = sorted(_DIR.glob("*.json"))
    if not paths:
        raise RuntimeError("no lead playbooks")
    books = tuple(_load(path) for path in paths)
    ids = [item.id for item in books]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate playbook id")
    return books


def playbook_by_id(niche_id: str) -> LeadPlaybook:
    found = next((item for item in load_playbooks() if item.id == niche_id), None)
    if found is None:
        known = ", ".join(item.id for item in load_playbooks()) or "none"
        raise RuntimeError(f"unknown niche: {niche_id}. Known playbooks: {known}")
    return found


def niche_menu() -> list[dict]:
    return [item.menu() for item in load_playbooks()]


def plan_from_playbook(analysis, playbook: LeadPlaybook) -> SourcePlan:
    """The playbook replaces a model source plan. Disabled catalog ids stay out."""
    selected = []
    selected_ids = set()
    for item in playbook.sources:
        if item["use"] != "collect":
            continue
        spec = source_by_id(item["id"])
        if spec is None or spec.status != "enabled":
            continue
        queries = playbook.queries() if spec.id == "searxng_web" else ()
        selected.append(PlannedSource(
            spec.id, item["note"], queries,
            {"geography": "United States", "playbook": playbook.id, "playbook_version": playbook.version,
             "minimum_usd_per_year": playbook.minimum_usd_per_year,
             "company_terms": [term for sub in playbook.subsegments for term in sub["terms"]]}))
        selected_ids.add(spec.id)
    if not selected:
        raise RuntimeError("playbook selected no enabled source")
    excluded = []
    for spec in CATALOG:
        if spec.id in selected_ids:
            continue
        note = playbook.source_note(spec.id)
        if spec.status != "enabled":
            why = f"{spec.disabled_reason} {note}".strip()
        else:
            why = note or f"Not selected by playbook {playbook.id}."
        excluded.append({"id": spec.id, "why": why})
    brief_hash = digest(analysis.brief_hash() + "\n" + playbook.id + "\n" + playbook.version)
    return SourcePlan(brief_hash, tuple(selected), tuple(excluded), model_used=False)


def annual_price_cents(text: str) -> tuple[int, ...]:
    """Published from-prices, in cents per year. A monthly figure is multiplied by 12."""
    found = []
    for match in _FROM_PRICE.finditer(text or ""):
        cents = _dollars_to_cents(match.group(1))
        if _PER_MONTH.match(text[match.end():match.end() + 16]):
            cents *= 12
        found.append(cents)
    return tuple(found)


def team_larger_than_one(text: str) -> bool:
    if _TEAM_OF.search(text or ""):
        return True
    if not _TEAM_WORDS.search(text or ""):
        return False
    return len({item.group(0).casefold() for item in _ROLES.finditer(text)}) >= 2


def signal_temperature(playbook: LeadPlaybook, signal: dict, *, age_days: int, context_only: bool) -> str:
    """Hot is a dated sales, hiring, or growth event inside 30 days.

    The same kind of event inside 90 days, or an undated sign the playbook
    marks warm, stays warm. Past 90 days the temperature is cold.
    """
    if age_days < 0 or age_days > playbook.context_days:
        return "cold"
    if context_only or age_days > playbook.event_days:
        return signal["context_temperature"]
    return signal["event_temperature"]


def screen_lead(playbook: LeadPlaybook, *, evidence: str, domain: str, quote: str,
                 age_days: int, context_only: bool) -> PlaybookDecision:
    """Fit rules for one stored company. The reason is a sentence copied from the quote."""
    for rule in playbook.exclusions:
        code = _exclusion(playbook, rule, evidence, domain)
        if code:
            return PlaybookDecision(rejection=code)
    if not _deal_shown(playbook, evidence):
        return PlaybookDecision(rejection="deal_size_not_shown")
    quotes = fit_quotes(playbook, evidence)
    if not quotes:
        return PlaybookDecision(rejection="no_page_reason")
    matched = _matching_signal(playbook, quote) or _matching_signal(playbook, evidence)
    if matched is None:
        kind, temperature = "page_reason", "cold"
    else:
        kind = matched["id"]
        temperature = signal_temperature(playbook, matched, age_days=age_days, context_only=context_only)
    return PlaybookDecision(
        kind=kind, temperature=temperature, segment=_segment(playbook, evidence),
        reason=quotes[0], quotes=quotes)


def _matching_signal(playbook: LeadPlaybook, text: str) -> dict | None:
    return next((item for item in playbook.signals if re.search(item["pattern"], text or "", re.I)), None)


def fit_quotes(playbook: LeadPlaybook, text: str) -> tuple[str, ...]:
    """One or two sentences about what the company sells and to whom.

    A glued menu or a skip-to-content run is not a quote. A missing event
    date is not a reason to drop the sentence.
    """
    found = []
    for part in re.findall(r"[^.!?]+[.!?]", text or ""):
        sentence = " ".join(part.split())
        if not sentence or len(sentence) > 280 or sentence in found:
            continue
        if _CHROME_SENTENCE.search(sentence) or _menu_sentence(sentence):
            continue
        if not _sells(playbook, sentence):
            continue
        if sentence not in " ".join((text or "").split()):
            continue
        found.append(sentence)
        if len(found) == 2:
            break
    return tuple(found)


def _menu_sentence(sentence: str) -> bool:
    words = sentence.split()
    if not words:
        return True
    first = words[0].strip(".,;:()[]")
    if first[:1].isdigit() or first.casefold() in {"com", "www"}:
        return True
    caps = sum(1 for word in words if word[:1].isupper())
    return len(words) > 8 and caps / len(words) > 0.72


_CHROME_SENTENCE = re.compile(
    r"\b(?:skip to content|toggle navigation|all rights reserved|cookie policy|privacy policy|accept cookies)\b",
    re.I,
)


def _sells(playbook: LeadPlaybook, sentence: str) -> bool:
    folded = sentence.casefold()
    for sub in playbook.subsegments:
        for term in sub["terms"]:
            if term.casefold() in folded:
                return True
    if re.search(r"\bfor b2b\b|\bb2b (?:companies|teams|clients|customers|businesses)\b", sentence, re.I):
        return True
    if re.search(r"\b(?:clients include|case study)\b", sentence, re.I) and re.search(r"\bb2b\b", sentence, re.I):
        return True
    floor = playbook.minimum_usd_per_year * 100
    return any(amount >= floor for amount in annual_price_cents(sentence))


def _exclusion(playbook: LeadPlaybook, rule: dict, evidence: str, domain: str) -> str:
    method = rule.get("method")
    if method == "already_applied":
        return ""
    if method == "shared_blocklist":
        return block_reason(domain)
    if method == "competitor_offer":
        return ""
    if method == "platform_domain":
        return rule["id"] if domain and is_platform(domain) else ""
    if method == "domain_list":
        host = registrable_domain(domain or "")
        return rule["id"] if host in set(rule.get("domains", ())) else ""
    if method == "low_ticket":
        floor = playbook.minimum_usd_per_year * 100
        return rule["id"] if any(amount < floor for amount in annual_price_cents(evidence)) else ""
    if rule.get("pattern") and re.search(rule["pattern"], evidence or "", re.I):
        return rule["id"]
    return ""


def _deal_shown(playbook: LeadPlaybook, evidence: str) -> bool:
    floor = playbook.minimum_usd_per_year * 100
    if any(amount >= floor for amount in annual_price_cents(evidence)):
        return True
    for sign in playbook.deal_signs:
        if sign.get("method") == "from_price":
            continue
        if sign.get("method") == "team_larger_than_one" and team_larger_than_one(evidence):
            return True
        if sign.get("pattern") and re.search(sign["pattern"], evidence or "", re.I):
            return True
    return False


def _signal_sentence(quote: str, pattern: str) -> str:
    compiled = re.compile(pattern, re.I)
    for part in re.findall(r"[^.!?]+[.!?]", quote or ""):
        sentence = part.strip()
        if sentence and len(sentence) <= 280 and compiled.search(sentence) and sentence in quote:
            return sentence
    return ""


_PAGE_RANK = ("careers", "jobs", "blog", "news", "case-studies", "cases", "pricing", "about", "team", "contact", "clients", "work")
_SALES_FIRST = {"sales_hire": 0, "open_sales_role": 1}


def find_company_page_signals(playbook: LeadPlaybook, pages) -> tuple[dict, ...]:
    """Every signal sentence on the company's own pages.

    A date is taken from the page's publication markup or from a date written
    in the sentence. The collection clock is not a date. Without either, the
    signal is context and cannot be hot.
    """
    from evorove_lead.company_facts import date_in_sentence, parse_page_date
    found = []
    for item in pages:
        url, text = item[0], item[1]
        published = item[2] if len(item) > 2 else ""
        language = item[3] if len(item) > 3 else ""
        if not looks_english(text or "", language=language or ""):
            continue
        for index, signal in enumerate(playbook.signals):
            sentence = _signal_sentence(text, signal["pattern"])
            if not sentence:
                continue
            explicit = (published or "").strip() or date_in_sentence(sentence)
            if explicit and parse_page_date(explicit) is None:
                explicit = ""
            found.append({
                "quote": sentence,
                "source_url": url,
                "source_text": text,
                "signal_type": signal["id"],
                "published": explicit,
                "context_only": not explicit,
                "_rank": (0 if explicit else 1, _SALES_FIRST.get(signal["id"], 5), _page_rank(url), index),
            })
    found.sort(key=lambda item: item["_rank"])
    for item in found:
        item.pop("_rank", None)
    return tuple(found)


def find_company_page_signal(playbook: LeadPlaybook, pages) -> dict | None:
    """The signal kept for one company: a dated sales sentence beats the rest."""
    found = find_company_page_signals(playbook, pages)
    return found[0] if found else None


def _page_rank(url: str) -> int:
    path = urlsplit(url).path.casefold()
    for index, name in enumerate(_PAGE_RANK):
        if re.search(rf"(?:^|/){re.escape(name)}(?:/|$)", path):
            return index
    return len(_PAGE_RANK)


def _segment(playbook: LeadPlaybook, evidence: str) -> str:
    folded = (evidence or "").casefold()
    best = ""
    length = 0
    for sub in playbook.subsegments:
        for term in sub["terms"]:
            if term.casefold() in folded and len(term) > length:
                best = sub["title"]
                length = len(term)
    return best or playbook.title


def _dollars_to_cents(raw: str) -> int:
    whole, _, frac = raw.replace(",", "").partition(".")
    frac = (frac + "00")[:2]
    return int(whole) * 100 + int(frac)


def _load(path: Path) -> LeadPlaybook:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("describes") != "lead_segments":
        raise RuntimeError(f"{path.name} must describe lead_segments")
    freshness = data["freshness"]
    event_days = int(freshness["event_days"])
    context_days = int(freshness["context_days"])
    if event_days != 30 or context_days != 90:
        raise RuntimeError(f"{path.name} freshness must be 30 event days and 90 context days")
    minimum = int(data["minimum_usd_per_year"])
    if minimum != 5000:
        raise RuntimeError(f"{path.name} minimum deal must be 5000 USD a year")
    subsegments = tuple(data["subsegments"])
    queries = []
    for sub in subsegments:
        if not sub.get("id") or not sub.get("title") or not sub.get("terms"):
            raise RuntimeError(f"{path.name} subsegment")
        status = sub.get("status") or "active"
        if status not in {"active", "needs_research"}:
            raise RuntimeError(f"{path.name} subsegment status")
        if status == "needs_research" and "needs_research" not in (sub.get("why") or ""):
            raise RuntimeError(f"{path.name} subsegment needs_research")
        for query in sub["queries"]:
            _check_query(path.name, query)
            queries.append(query)
    if len(queries) != len(set(queries)):
        raise RuntimeError(f"{path.name} duplicate query")
    if len(queries) > 30:
        raise RuntimeError(f"{path.name} has more queries than one check may run")
    signals = tuple(data["signals"])
    for signal in signals:
        if signal.get("event_temperature") not in _TEMPERATURES or signal.get("context_temperature") not in _TEMPERATURES:
            raise RuntimeError(f"{path.name} signal temperature")
        re.compile(signal["pattern"])
    for sign in data["deal_size"]["signs"]:
        if sign.get("pattern"):
            re.compile(sign["pattern"])
        elif sign.get("method") not in {"from_price", "team_larger_than_one"}:
            raise RuntimeError(f"{path.name} deal sign")
    exclusions = tuple(data["exclusions"])
    for rule in exclusions:
        method = rule.get("method")
        if method == "already_applied":
            continue
        if method == "domain_list":
            if not rule.get("domains"):
                raise RuntimeError(f"{path.name} domain list")
            continue
        if method in {"platform_domain", "low_ticket", "shared_blocklist", "competitor_offer"}:
            continue
        if method not in {None, ""} or not rule.get("pattern"):
            raise RuntimeError(f"{path.name} exclusion")
        re.compile(rule["pattern"])
    sources = tuple(data["sources"])
    seen = [item["id"] for item in sources]
    if set(seen) != {spec.id for spec in CATALOG} or len(seen) != len(set(seen)):
        raise RuntimeError(f"{path.name} must name each catalog source once")
    for item in sources:
        spec = source_by_id(item["id"])
        use = item.get("use")
        if use not in _USES or not item.get("note"):
            raise RuntimeError(f"{path.name} source {item['id']}")
        if use == "collect" and spec.status != "enabled":
            raise RuntimeError(f"{path.name} collects a disabled source")
        if use == "skip" and spec.status != "enabled":
            raise RuntimeError(f"{path.name} skips a disabled source")
        if use == "unavailable" and spec.status != "disabled":
            raise RuntimeError(f"{path.name} marks an enabled source unavailable")
        if use != "collect" and "needs_research" not in item["note"]:
            raise RuntimeError(f"{path.name} source {item['id']} needs_research")
    gaps = tuple(data["gaps"])
    catalog_ids = {spec.id for spec in CATALOG}
    for gap in gaps:
        if gap.get("status") != "needs_research" or gap["id"] in catalog_ids or "needs_research" not in gap["why"]:
            raise RuntimeError(f"{path.name} gap")
    angles = tuple(data["touch_angles"])
    if len(angles) < 3:
        raise RuntimeError(f"{path.name} touch angles")
    return LeadPlaybook(
        id=data["id"], title=data["title"], version=data["version"], describes=data["describes"],
        lead_summary=data["lead_summary"], client_note=data["client_note"],
        event_days=event_days, context_days=context_days, minimum_usd_per_year=minimum,
        subsegments=subsegments, signals=signals, deal_signs=tuple(data["deal_size"]["signs"]),
        deal_how=data["deal_size"]["how"], exclusions=exclusions, sources=sources, gaps=gaps,
        touch_angles=angles)


def _check_query(name: str, query: str) -> None:
    if not isinstance(query, str) or not 8 <= len(query) <= 120 or '"' in query or "?" in query:
        raise RuntimeError(f"{name} query")
    for token in _QUERY_TOKEN.findall(query):
        folded = token.casefold().strip(".,")
        if folded == "site":
            raise RuntimeError(f"{name} query must not contain the word site")
        if folded in _QUERY_FORBIDDEN:
            raise RuntimeError(f"{name} query must not search for {folded}")
        if folded.startswith("site:") and not re.fullmatch(r"site:[a-z0-9.-]+", folded):
            raise RuntimeError(f"{name} query has a site token that is not an operator")
