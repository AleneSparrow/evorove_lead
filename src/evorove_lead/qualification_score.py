"""Point score for a stored company. Weights live in data/qualification_score.json.

A hard reject is only an explicit disqualifier. A missing name, contact,
country sign, or event lowers Reach, Fit, or Intent and does not reject.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from evorove_lead.company_facts import assess_us, date_in_sentence, parse_page_date
from evorove_lead.competitor_offers import competitor_offer
from evorove_lead.domain_blocklist import block_reason
from evorove_lead.lead_playbooks import annual_price_cents, fit_quotes, team_larger_than_one
from evorove_lead.platforms import is_platform, registrable_domain

_PATH = Path(__file__).resolve().parent / "data" / "qualification_score.json"
_SCHEMA_COUNTRY = re.compile(
    r"""addressCountry(?:"\s*:\s*"|\s+)([A-Za-z][A-Za-z. ]{1,32})""",
)
_HEADCOUNT = re.compile(
    r"\b(?:team of|about|around|approximately|over|nearly)?\s*(\d{1,5})\s+"
    r"(?:people|employees|person team|staff)\b",
    re.I,
)
_PAGE_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+20\d{2}\b|\b20\d{2}-\d{2}-\d{2}\b",
    re.I,
)
_DECISION_ROLE = re.compile(
    r"\b(co-founder|cofounder|founder|co-owner|owner|ceo|president|managing director|head of sales)\b",
    re.I,
)


@dataclass(frozen=True)
class ScoreResult:
    rejection: str = ""
    fit: int = 0
    intent: int = 0
    reach: int = 0
    total: int = 0
    level: str = ""
    matched: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()

    @property
    def cold_ready(self) -> bool:
        return self.level == "A" and not self.rejection and bool(self.quotes)


def load_score_model(path: Path | None = None) -> dict:
    data = json.loads((path or _PATH).read_text(encoding="utf-8"))
    if data.get("describes") != "qualification_score":
        raise RuntimeError("qualification score file must describe qualification_score")
    fit_max = int(data["fit"]["max"])
    intent_max = int(data["intent"]["max"])
    reach_max = int(data["reach"]["max"])
    if (fit_max, intent_max, reach_max) != (50, 30, 20):
        raise RuntimeError("qualification axes must be 50, 30, and 20")
    if int(data["levels"]["A"]) != 70 or int(data["levels"]["B"]) != 50:
        raise RuntimeError("qualification levels must be 70 and 50")
    if int(data["minimum_usd_per_year"]) != 5000:
        raise RuntimeError("qualification floor must be 5000 USD a year")
    fit_points = 0
    for group in data["fit"]["groups"]:
        if group["id"] == "us":
            fit_points += max(group["points_by_strength"].values())
        else:
            fit_points += int(group["points"])
    if fit_points != fit_max:
        raise RuntimeError("fit group points must sum to the fit maximum")
    if sum(int(item["points"]) for item in data["intent"]["items"]) != intent_max:
        raise RuntimeError("intent item points must sum to the intent maximum")
    if sum(int(item["points"]) for item in data["reach"]["items"]) != reach_max:
        raise RuntimeError("reach item points must sum to the reach maximum")
    return data


def level_for_total(total: int, model: dict | None = None) -> str:
    """A is 70 and above, B is 50 to 69, C is below 50."""
    data = model or load_score_model()
    if total >= int(data["levels"]["A"]):
        return "A"
    if total >= int(data["levels"]["B"]):
        return "B"
    return "C"


def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


def schema_country(text: str, declared: str = "") -> str:
    raw = (declared or "").strip()
    if not raw:
        match = _SCHEMA_COUNTRY.search(text or "")
        raw = match.group(1).strip(" .\"'") if match else ""
    return raw


def strong_non_us(text: str, *, schema: str = "", model: dict | None = None) -> bool:
    """Schema.org country other than the US, or an explicit non-US place.

    No US sign at all is not this. A weak or missing US sign only lowers Fit.
    """
    data = model or load_score_model()
    allowed = {item.casefold() for item in data["not_us"]["schema_countries_us"]}
    country = schema_country(text, schema)
    if country and country.casefold().rstrip(".") not in allowed:
        return True
    return bool(_compiled(data["not_us"]["place_pattern"]).search(text or ""))


def _headcount_in_band(text: str) -> bool:
    for match in _HEADCOUNT.finditer(text or ""):
        number = int(match.group(1))
        if 10 <= number <= 200:
            return True
    return False


def _fresh_date(text: str, *, now: datetime, max_age_days: int) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    for match in _PAGE_DATE.finditer(text or ""):
        parsed = parse_page_date(match.group(0)) or parse_page_date(date_in_sentence(match.group(0)))
        if parsed is None:
            continue
        age = (now - parsed.astimezone(timezone.utc)).days
        if 0 <= age <= max_age_days:
            return True
    return False


def _role_ok(role: str, roles: tuple[str, ...] | list[str]) -> bool:
    folded = (role or "").casefold()
    return any(item.casefold() in folded for item in roles)


@dataclass
class ScoreInput:
    evidence: str
    domain: str = ""
    person_name: str = ""
    person_role: str = ""
    email_mx: bool = False
    contact_form: str = ""
    schema: str = ""
    now: datetime = field(default_factory=lambda: datetime(2026, 10, 26, tzinfo=timezone.utc))
    minimum_usd_per_year: int | None = None


def score_company(item: ScoreInput, model: dict | None = None) -> ScoreResult:
    """Score one company from saved text. Disqualifiers return no points."""
    data = model or load_score_model()
    floor = int(item.minimum_usd_per_year or data["minimum_usd_per_year"]) * 100
    evidence = item.evidence or ""
    rejection = _disqualify(item, data, floor)
    if rejection:
        return ScoreResult(rejection=rejection)
    matched: list[str] = []
    fit = _fit_points(evidence, data, floor, matched)
    intent = _intent_points(evidence, data, item.now, matched)
    reach = _reach_points(item, data, matched)
    total = fit + intent + reach
    quotes = fit_quotes_from_evidence(evidence)
    gaps = _gaps(data, matched, quotes)
    return ScoreResult(
        fit=fit, intent=intent, reach=reach, total=total,
        level=level_for_total(total, data), matched=tuple(matched),
        gaps=tuple(gaps), quotes=quotes,
    )


def fit_quotes_from_evidence(evidence: str) -> tuple[str, ...]:
    """Sell-sentences already accepted by the playbook quote rule, when a playbook loads.

    Imported lazily so a score of plain text still works if the playbook is present.
    """
    from evorove_lead.lead_playbooks import playbook_by_id
    return fit_quotes(playbook_by_id("evorove_icp"), evidence)


def _disqualify(item: ScoreInput, data: dict, floor_cents: int) -> str:
    evidence = item.evidence or ""
    domain = item.domain or ""
    for rule in data["disqualifiers"]:
        method = rule.get("method")
        if method == "competitor_offer":
            if competitor_offer(evidence):
                return rule["id"]
            continue
        if method == "strong_non_us":
            if strong_non_us(evidence, schema=item.schema, model=data):
                return rule["id"]
            continue
        if method == "from_price_below_floor":
            if any(amount < floor_cents for amount in annual_price_cents(evidence)):
                return rule["id"]
            continue
        if method == "platform_or_aggregator":
            host = registrable_domain(domain)
            if domain and (is_platform(domain) or block_reason(host) in {"list_catalog", "marketplace", "job_board", "social"}):
                return rule["id"]
            continue
        if rule.get("pattern") and _compiled(rule["pattern"]).search(evidence):
            return rule["id"]
    return ""


def _explicit_headcounts(text: str) -> tuple[int, ...]:
    return tuple(int(match.group(1)) for match in _HEADCOUNT.finditer(text or ""))


def _fit_points(evidence: str, data: dict, floor_cents: int, matched: list[str]) -> int:
    total = 0
    headcounts = _explicit_headcounts(evidence)
    for group in data["fit"]["groups"]:
        if group["id"] == "us":
            verdict = assess_us(evidence)
            if verdict["strong"]:
                total += int(group["points_by_strength"]["strong"])
                matched.append("us_strong")
            elif verdict["weak"]:
                total += int(group["points_by_strength"]["weak"])
                matched.append("us_weak")
            continue
        hit = False
        for spec in group.get("items", ()):
            if group["id"] == "size" and spec.get("method") == "team_sign" and headcounts and not any(10 <= n <= 200 for n in headcounts):
                continue
            if _item_matches(spec, evidence, floor_cents):
                hit = True
                matched.append(spec["id"])
                if group.get("stack") is False:
                    break
        if hit:
            total += int(group["points"])
    cap = int(data["fit"]["max"])
    return min(total, cap)


def _intent_points(evidence: str, data: dict, now: datetime, matched: list[str]) -> int:
    total = 0
    for spec in data["intent"]["items"]:
        if spec.get("method") == "fresh_page_date":
            ok = _fresh_date(evidence, now=now, max_age_days=int(spec.get("max_age_days", 90)))
        else:
            ok = bool(_compiled(spec["pattern"]).search(evidence))
        if ok:
            total += int(spec["points"])
            matched.append(spec["id"])
    return min(total, int(data["intent"]["max"]))


def _reach_points(item: ScoreInput, data: dict, matched: list[str]) -> int:
    total = 0
    for spec in data["reach"]["items"]:
        if spec["id"] == "named_decision_maker":
            roles = spec["roles"]
            named = bool((item.person_name or "").strip())
            ok = named and (
                _role_ok(item.person_role, roles) or bool(_DECISION_ROLE.search(item.evidence or ""))
            )
        elif spec.get("method") == "published_email_with_mx":
            ok = bool(item.email_mx)
        elif spec.get("method") == "contact_form":
            ok = bool((item.contact_form or "").strip())
        else:
            ok = False
        if ok:
            total += int(spec["points"])
            matched.append(spec["id"])
    return min(total, int(data["reach"]["max"]))


def _item_matches(spec: dict, evidence: str, floor_cents: int) -> bool:
    method = spec.get("method")
    if method == "headcount_10_200":
        return _headcount_in_band(evidence)
    if method == "team_sign":
        return team_larger_than_one(evidence)
    if method == "from_price_at_or_above_floor":
        return any(amount >= floor_cents for amount in annual_price_cents(evidence))
    if spec.get("pattern"):
        return bool(_compiled(spec["pattern"]).search(evidence))
    return False


def _gaps(data: dict, matched: list[str], quotes: tuple[str, ...]) -> list[str]:
    found = set(matched)
    gaps = []
    if "b2b_services" not in found and "b2b_software" not in found:
        gaps.append("b2b")
    if "us_strong" not in found and "us_weak" not in found:
        gaps.append("us")
    if not ({"headcount", "offices", "team"} & found):
        gaps.append("size")
    if not ({"book_a_call", "custom_pricing", "b2b_case_study", "from_price"} & found):
        gaps.append("deal")
    for spec in data["intent"]["items"]:
        if spec["id"] not in found:
            gaps.append(spec["id"])
    for spec in data["reach"]["items"]:
        if spec["id"] not in found:
            gaps.append(spec["id"])
    if not quotes:
        gaps.append("quote")
    return gaps
