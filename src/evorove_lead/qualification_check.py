"""Score companies already stored in a source database. Read-only. No CRM write.

The SEC ticker file is downloaded only when its size is known to be at or under
50 MB, or when a streaming read finishes under that cap. A file that passes the
cap is discarded.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text

from evorove_lead.decision_maker import find_named_leader
from evorove_lead.lead_playbooks import playbook_by_id, screen_lead
from evorove_lead.mass_sources import MAX_DOWNLOAD_BYTES, dedupe_companies, fits_portrait, parse_company_tickers, read_capped
from evorove_lead.platforms import is_platform
from evorove_lead.source_collection import public_mx_exists

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_UA = "EvoroveLead/0.1 (+https://evorove.com; public cycle-1 research)"


def score_saved(engine, *, business_id: str | None = None, mx_check=public_mx_exists) -> dict:
    book = playbook_by_id("evorove_icp")
    with engine.connect() as conn:
        companies = conn.execute(text(
            "select business_id, domain, name, site_url from companies order by domain"
        )).mappings().all()
        if business_id:
            companies = [row for row in companies if row["business_id"] == business_id]
        pages = conn.execute(text(
            "select business_id, domain, url, text from raw_pages"
        )).mappings().all()
        signals = conn.execute(text(
            "select business_id, domain, quote, source_text, signal_type, context_only from signals"
        )).mappings().all()
    by_page = defaultdict(list)
    for page in pages:
        by_page[(page["business_id"], page["domain"])].append(page)
    by_signal = defaultdict(list)
    for signal in signals:
        by_signal[(signal["business_id"], signal["domain"])].append(signal)
    scored = []
    for company in companies:
        key = (company["business_id"], company["domain"])
        page_rows = by_page.get(key, [])
        signal_rows = by_signal.get(key, [])
        parts = [row["text"] or "" for row in page_rows]
        parts.extend((row["source_text"] or "") for row in signal_rows)
        evidence = "\n".join(part for part in parts if part)
        pages_for_leader = tuple((row["url"], row["text"] or "", ()) for row in page_rows)
        leader = find_named_leader(pages_for_leader) if pages_for_leader else None
        person_name, person_role = (leader[0], leader[1]) if leader else ("", "")
        emails = sorted({item.casefold() for item in _EMAIL.findall(evidence)})
        own = [item for item in emails if item.endswith("@" + company["domain"]) or item.endswith("." + company["domain"])]
        email_mx = bool(own) and any(mx_check(company["domain"]) for _ in own[:1])
        form = ""
        if "contact_form " in evidence:
            form = evidence.split("contact_form ", 1)[1].split()[0]
        quote = ""
        for signal in signal_rows:
            saved = signal["source_text"] or ""
            if signal["quote"] and signal["quote"] in saved:
                quote = signal["quote"]
                break
        decision = screen_lead(
            book, evidence=evidence, domain=company["domain"], quote=quote,
            age_days=1, context_only=True, person_name=person_name, person_role=person_role,
            email_mx=email_mx, contact_form=form,
        )
        scored.append({
            "domain": company["domain"],
            "name": company["name"],
            "person": person_name,
            "role": person_role,
            "emails_on_page": len(own),
            "email_mx": email_mx,
            "form": form,
            "quote": (decision.reason or "")[:280],
            "fit": decision.fit,
            "intent": decision.intent,
            "reach": decision.reach,
            "total": decision.total,
            "level": decision.level,
            "rejection": decision.rejection,
            "temperature": decision.temperature,
            "gaps": list(decision.gaps),
            "platform": is_platform(company["domain"]),
        })
    levels = {"A": 0, "B": 0, "C": 0}
    reasons = {}
    for row in scored:
        if row["rejection"]:
            reasons[row["rejection"]] = reasons.get(row["rejection"], 0) + 1
        elif row["level"] in levels:
            levels[row["level"]] += 1
    return {"companies": len(scored), "levels": levels, "disqualify": reasons, "rows": scored}


def load_tickers(opener=urlopen) -> dict:
    """One GET. Refuse a body that reaches the 50 MB cap."""
    request = Request(_TICKER_URL, headers={"User-Agent": _UA, "Accept": "application/json"})
    chunks = []

    def blocks():
        with opener(request, timeout=30) as response:
            while True:
                block = response.read(65536)
                if not block:
                    break
                yield block

    body, size = read_capped(blocks(), MAX_DOWNLOAD_BYTES)
    if body is None:
        return {"requests": 1, "downloaded": False, "bytes_seen": size, "rows": 0, "fit": 0}
    rows = dedupe_companies(parse_company_tickers(body.decode("utf-8", "replace")))
    fit = sum(1 for row in rows if fits_portrait(row["name"], row.get("naics") or ""))
    return {"requests": 1, "downloaded": True, "bytes": size, "rows": len(rows), "fit": fit}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--skip-sec", action="store_true")
    args = parser.parse_args(argv)
    engine = create_engine(args.database_url, connect_args={"options": "-c default_transaction_read_only=on"})
    report = score_saved(engine)
    report["check_requests"] = 0
    if not args.skip_sec:
        sec = load_tickers()
        report["sec"] = {key: value for key, value in sec.items() if key != "rows_body"}
        report["check_requests"] = sec["requests"]
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False))
    print("---ROWS---")
    for row in sorted(report["rows"], key=lambda item: (-(item["total"] or 0), item["domain"])):
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
