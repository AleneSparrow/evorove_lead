"""Cycle 1's own transparency screen: what was searched, why, and what came of it.

A separate surface from the CRM board on purpose -- the CRM shows the four
tabs of the sales board (Cold -> ... -> Done); this shows how cycle 1 got
there: the brief, the hypotheses (audience + criteria), every trace it
looked at, why a trace was rejected, and which candidates became Cold.
Read-only. Writes nothing. Sends nothing.

Deliberately no template engine or JS framework, in keeping with the rest
of this repo -- plain server-rendered HTML, escaped by hand.
"""

from __future__ import annotations

import hmac
import os
from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from evorove_lead.warehouse import (
    AnalysisWarehouse,
    BriefRecord,
    CandidateRecord,
    HypothesisRecord,
    RejectedTraceRecord,
    TraceRecord,
)

_security = HTTPBasic(auto_error=False)
_INSIGHTS_USER = "owner"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="robots" content="noindex, nofollow">
<style>
  body {{ font: 15px/1.5 -apple-system, sans-serif; max-width: 880px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; }}
  h1 {{ font-size: 20px; }}
  h2 {{ font-size: 16px; margin-top: 32px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  .muted {{ color: #767676; }}
  .card {{ border: 1px solid #ddd; border-radius: 6px; padding: 12px 16px; margin: 10px 0; }}
  .rejected {{ border-left: 3px solid #c0392b; }}
  .accepted {{ border-left: 3px solid #2e7d32; }}
  .tag {{ display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 10px; background: #eee; margin-right: 6px; }}
  .raw {{ white-space: pre-wrap; font-size: 13px; color: #444; max-height: 6em; overflow: hidden; }}
  a {{ color: #1a1a1a; }}
  form {{ margin: 16px 0; }}
  input[type=text] {{ padding: 6px 8px; width: 260px; }}
  button {{ padding: 6px 12px; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    configured = os.getenv("INSIGHTS_PASSWORD") or ""
    if not configured:
        raise HTTPException(status_code=503, detail="insights screen is not enabled (INSIGHTS_PASSWORD)")
    valid_user = credentials is not None and hmac.compare_digest(credentials.username, _INSIGHTS_USER)
    valid_pass = credentials is not None and hmac.compare_digest(credentials.password, configured)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=401,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def _index_page(default_business_id: str) -> str:
    body = f"""
<h1>Cycle 1 — search &amp; analysis log</h1>
<p class="muted">Read-only. Shows the brief, the search criteria, every trace looked at,
why a trace was rejected, and which candidates reached the CRM's Cold tab. Nothing here
sends a message or books anything.</p>
<form action="/insights/business" method="get">
  <input type="text" name="business_id" placeholder="business_id" value="{escape(default_business_id)}" required>
  <button type="submit">View</button>
</form>
"""
    return _PAGE.format(title="Cycle 1 — search log", body=body)


def _fmt_pairs(pairs: tuple[dict[str, str], ...]) -> str:
    """Renders `GroundedClaim`/`CommercialClaim` dicts: {text, source_name[, kind]}."""
    if not pairs:
        return '<span class="muted">(none)</span>'
    rows = []
    for p in pairs:
        kind = p.get("kind")
        kind_tag = f'<span class="tag">{escape(str(kind))}</span> ' if kind else ""
        text = escape(str(p.get("text", "")))
        source = escape(str(p.get("source_name", "")))
        rows.append(f"<li>{kind_tag}{text} <span class=\"muted\">(source: {source})</span></li>")
    return f"<ul>{''.join(rows)}</ul>"


def _marketing_block(analysis: dict | None) -> str:
    """AI reading, rendered apart from the literal quotes above it."""

    if not analysis:
        return ""

    def fact_row(label: str, fact: object) -> str:
        if not isinstance(fact, dict) or not fact.get("quote"):
            return f"<li><strong>{escape(label)}</strong> <span class=\"muted\">not stated in the materials</span></li>"
        quote = escape(str(fact.get("quote", "")))
        source = escape(str(fact.get("source_name", "")))
        return (
            f"<li><strong>{escape(label)}</strong> {quote} "
            f"<span class=\"muted\">quoted from the client's materials (source: {source})</span></li>"
        )

    facts = "".join(
        fact_row(label, analysis.get(key))
        for label, key in (
            ("Product", "product"),
            ("Price", "price"),
            ("Place", "place"),
            ("Promotion", "promotion"),
        )
    )
    segments = analysis.get("segments") if isinstance(analysis.get("segments"), list) else []
    segment_rows = "".join(
        f"<li><span class=\"tag\">AI inference</span> {escape(str(segment.get('label', '')))} "
        f"via {escape(str(segment.get('channel', '')))}"
        f"<div>Evidence quoted from the client's materials: {escape(str(segment.get('evidence_quote', '')))} "
        f"<span class=\"muted\">({escape(str(segment.get('source_name', '')))})</span></div></li>"
        for segment in segments
        if isinstance(segment, dict)
    )
    return f"""
<h2>Marketing reading</h2>
<p class="muted">4P lines are quotes. Segment labels are an AI inference, each tied to a quote.</p>
<ul>{facts}</ul>
<p><strong>Inferred audiences</strong></p>
<ul>{segment_rows}</ul>
"""


def _brief_section(briefs: tuple[BriefRecord, ...]) -> str:
    if not briefs:
        return '<h2>Brief</h2><p class="muted">No brief run yet for this business.</p>'
    b = briefs[-1]
    must_not = "".join(f"<li>{escape(x)}</li>" for x in b.must_not_promise) or '<li class="muted">(none)</li>'
    return f"""
<h2>Brief — what the engine understood about this business</h2>
<p class="muted">Formed {escape(b.created_at.isoformat())} · archetype: {escape(b.business_archetype or '—')}</p>
<p><strong>What we sell</strong></p>{_fmt_pairs(b.what_we_sell)}
<p><strong>Who may fit — quoted from the client's materials</strong></p>{_fmt_pairs(b.who_may_fit)}
{_marketing_block(b.marketing_analysis)}
<p><strong>Commercial claims on file</strong></p>{_fmt_pairs(b.commercial_claims)}
<p><strong>Must never promise</strong></p><ul>{must_not}</ul>
"""


def _hypothesis_card(
    h: HypothesisRecord,
    traces: tuple[TraceRecord, ...],
    candidates: tuple[CandidateRecord, ...],
) -> str:
    accepted = [c for c in candidates if c.decision == "cold"]
    rejected = [c for c in candidates if c.decision != "cold"]
    trace_rows = "".join(
        f'<div class="card"><span class="tag">{escape(t.source_channel)}</span>'
        f'<a href="{escape(t.url)}" target="_blank" rel="noopener">{escape(t.url)}</a>'
        f'<div class="muted">query: "{escape(t.query_used)}" · fetched {escape(t.fetched_at.isoformat())}</div>'
        f'<div class="raw">{escape(t.raw_text[:400])}</div></div>'
        for t in traces
    ) or '<p class="muted">No traces recorded yet.</p>'
    candidate_rows = "".join(
        f'<div class="card {"accepted" if c.decision == "cold" else "rejected"}">'
        f'<span class="tag">{escape(c.decision)}</span> <strong>{escape(c.identity)}</strong> '
        f'via {escape(c.channel)}'
        f'<div>reason: {escape(c.reason)} <span class="muted">({escape(c.reason_source)})</span></div>'
        f'<div class="muted">fit {c.fit:.2f} · evidence {c.evidence:.2f} · addressable: {c.addressable}</div>'
        f'</div>'
        for c in list(accepted) + list(rejected)
    ) or '<p class="muted">No candidates decided yet.</p>'
    if h.audience_source == "ai_inferred":
        source_line = (
            f'<p><span class="tag">AI inference</span> '
            f'Evidence quoted from the client\'s materials: {escape(h.evidence_quote)}</p>'
        )
    else:
        source_line = '<p><span class="tag">quoted from the client\'s materials</span></p>'
    return f"""
<div class="card">
  <p><strong>{escape(h.audience_segment)}</strong> via {escape(h.channel)}
     <span class="tag">{escape(h.status)}</span></p>
  {source_line}
  <p class="muted">query template: "{escape(h.query_template)}" · trigger: {escape(h.intent_trigger)}</p>
  <p class="muted">fit {h.fit_score:.2f} · evidence {h.evidence_score:.2f} · reach est. {h.reach_estimate}
     · accept rate {h.accept_rate:.0%} · close rate {h.close_rate:.0%}</p>
  <details><summary>{len(traces)} trace(s) looked at</summary>{trace_rows}</details>
  <details open><summary>{len(candidates)} candidate(s) decided — {len(accepted)} reached Cold</summary>{candidate_rows}</details>
</div>
"""


def _rejected_traces_section(rejected: tuple[RejectedTraceRecord, ...]) -> str:
    if not rejected:
        return '<h2>Rejected traces (never became a candidate)</h2><p class="muted">None.</p>'
    rows = "".join(
        f'<div class="card rejected">{escape(r.reason)} '
        f'<span class="muted">{escape(r.rejected_at.isoformat())}</span></div>'
        for r in rejected
    )
    return f"<h2>Rejected traces (never became a candidate) — {len(rejected)}</h2>{rows}"


def _business_page(
    business_id: str,
    briefs: tuple[BriefRecord, ...],
    hypotheses: tuple[HypothesisRecord, ...],
    traces_by_hypothesis: dict[str, tuple[TraceRecord, ...]],
    candidates_by_hypothesis: dict[str, tuple[CandidateRecord, ...]],
    rejected: tuple[RejectedTraceRecord, ...],
) -> str:
    hypothesis_html = "".join(
        _hypothesis_card(
            h,
            traces_by_hypothesis.get(h.id, ()),
            candidates_by_hypothesis.get(h.id, ()),
        )
        for h in hypotheses
    ) or '<p class="muted">No hypotheses formed yet.</p>'
    total_cold = sum(
        1 for cands in candidates_by_hypothesis.values() for c in cands if c.decision == "cold"
    )
    body = f"""
<p><a href="/insights">&larr; another business</a></p>
<h1>{escape(business_id)}</h1>
<p class="muted">{len(hypotheses)} hypothesis(es) · {total_cold} candidate(s) reached Cold ·
{len(rejected)} trace(s) rejected outright</p>
{_brief_section(briefs)}
<h2>Hypotheses — the search criteria, and what each turned up</h2>
{hypothesis_html}
{_rejected_traces_section(rejected)}
"""
    return _PAGE.format(title=f"Cycle 1 — {escape(business_id)}", body=body)


def register_insights_routes(app, warehouse: AnalysisWarehouse) -> None:
    """Mounts the read-only /insights screen on an existing FastAPI app."""

    router = APIRouter()

    @router.get("/insights", response_class=HTMLResponse)
    def insights_index(_: None = Depends(_require_auth)) -> HTMLResponse:
        default_business_id = os.getenv("EVOROVE_CLIENT_ZERO_BUSINESS_ID") or ""
        return HTMLResponse(_index_page(default_business_id))

    @router.get("/insights/business", response_class=HTMLResponse)
    def insights_business(business_id: str, _: None = Depends(_require_auth)) -> HTMLResponse:
        briefs = tuple(warehouse.list_briefs(business_id))
        hypotheses = tuple(warehouse.list_hypotheses(business_id))
        traces_by_hypothesis = {
            h.id: tuple(warehouse.list_traces(business_id, h.id)) for h in hypotheses
        }
        candidates_by_hypothesis = {
            h.id: tuple(warehouse.list_candidates(business_id, h.id)) for h in hypotheses
        }
        rejected = tuple(warehouse.list_rejected_traces(business_id))
        return HTMLResponse(
            _business_page(
                business_id,
                briefs,
                hypotheses,
                traces_by_hypothesis,
                candidates_by_hypothesis,
                rejected,
            )
        )

    app.include_router(router)
