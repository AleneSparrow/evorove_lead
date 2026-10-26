"""Keyless company lists. A source whose robots or terms forbid access is not fetched.

SEC company tickers are public and the path is outside the SEC robots disallow list.
Wikidata Query Service robots disallow /sparql, and the entity dump is larger than 50 MB.
State registry hosts checked in step 31 answered 403, a challenge, or Disallow: /.
SAM.gov Entity API returned HTTP 404 with no key and no entity body.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from evorove_lead.platforms import registrable_domain

_CODES = Path(__file__).resolve().parent / "data" / "portrait_codes.json"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def load_portrait_codes(path: Path | None = None) -> dict:
    data = json.loads((path or _CODES).read_text(encoding="utf-8"))
    if data.get("describes") != "portrait_classifier":
        raise RuntimeError("portrait file must describe portrait_classifier")
    return data


def fits_portrait(name: str, naics: str = "", codes: dict | None = None) -> bool:
    """Industry code and the company name. No model."""
    data = codes or load_portrait_codes()
    folded = (name or "").casefold()
    code = re.sub(r"\D", "", naics or "")
    if any(code.startswith(prefix) for prefix in data["naics_reject_prefixes"]):
        return False
    if any(term in folded for term in data["name_reject"]):
        return False
    if code and any(code.startswith(prefix) for prefix in data["naics_fit_prefixes"]):
        return True
    return any(term in folded for term in data["name_fit"])


def parse_company_tickers(payload: str) -> tuple[dict, ...]:
    """SEC company_tickers.json. Rows have a CIK, a ticker, and a title. No website."""
    data = json.loads(payload)
    rows = []
    seen = set()
    values = data.values() if isinstance(data, dict) else data
    for item in values:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        ticker = str(item.get("ticker") or "").strip().upper()
        cik = str(item.get("cik_str") or item.get("cik") or "").strip()
        if not title or not cik:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": title, "ticker": ticker, "cik": cik, "domain": "", "naics": ""})
    return tuple(rows)


def dedupe_companies(rows) -> tuple[dict, ...]:
    """One row per domain, and one row per name when the domain is empty."""
    seen_domains = set()
    seen_names = set()
    kept = []
    for row in rows:
        domain = registrable_domain(row.get("domain") or "")
        name = " ".join(str(row.get("name") or "").split())
        name_key = name.casefold()
        if domain:
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
        elif name_key in seen_names:
            continue
        if name_key:
            seen_names.add(name_key)
        kept.append({**row, "name": name, "domain": domain})
    return tuple(kept)


def read_capped(chunks, max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes | None, int]:
    """Stop once the stream passes the cap. The caller discards a partial body."""
    total = 0
    kept = []
    for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            return None, total
        kept.append(chunk)
    return b"".join(kept), total
