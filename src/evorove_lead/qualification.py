"""Evidence classes for a trace. Keyword overlap is not purchase intent.

Deterministic. No network. The engine still accepts or rejects through
`selection.select`; this module names the decision and, only when the
owner configured a priced cheap model, may confirm an inferred case.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from evorove_lead.selection import (
    EXPLICIT_INTENT,
    ICP_MATCH,
    INFERRED_OPPORTUNITY,
    INSUFFICIENT_EVIDENCE,
    IRRELEVANT,
    Selection,
    SelectionProfile,
    select,
)

CLASSES = (
    EXPLICIT_INTENT,
    INFERRED_OPPORTUNITY,
    ICP_MATCH,
    INSUFFICIENT_EVIDENCE,
    IRRELEVANT,
)

# A page that both asks for help and advertises a service is not a clean
# explicit intent. Left deterministic unless a cheap confirmer is configured.
_AMBIGUOUS = re.compile(
    r"\b(we (offer|provide|specialize)|call us|free estimate)\b.{0,80}\b(need|looking for)\b|"
    r"\b(need|looking for)\b.{0,80}\b(we (offer|provide|specialize)|call us|free estimate)\b",
    re.IGNORECASE | re.DOTALL,
)

_CONFIRM_SYSTEM = """You classify one public text against a US business's offer.
Return one JSON object and nothing else:
{"evidence_class": "EXPLICIT_INTENT"|"INFERRED_OPPORTUNITY"|"ICP_MATCH"|"INSUFFICIENT_EVIDENCE"|"IRRELEVANT", "evidence_quote": "<verbatim substring of the text, or empty>"}
EXPLICIT_INTENT requires the person to ask for the service or describe a broken need.
A keyword or audience mention without that ask is ICP_MATCH or INSUFFICIENT_EVIDENCE, never EXPLICIT_INTENT.
Do not invent a quote. Copy it from the text.
"""


@dataclass(frozen=True)
class Qualification:
    evidence_class: str
    accepted: bool
    why: str
    fit: float
    evidence: float
    ambiguous: bool


def qualify(
    text: str,
    profile: SelectionProfile,
    *,
    addressable: bool,
    source_kind: str,
    source_url: str,
) -> Qualification:
    """Same gate as `select`, plus a name for the evidence and a traceable source."""

    selection = select(text, profile, addressable=addressable, source_kind=source_kind)
    kind = selection.evidence_class or IRRELEVANT
    source = (source_url or "").strip()
    # A blank source is not traceable. An owner fixation ("copied public post,
    # date") is a source even when it is not an http URL; the web connector
    # already rejects non-public URLs before a hit exists.
    if kind in (EXPLICIT_INTENT, INFERRED_OPPORTUNITY, ICP_MATCH) and not source:
        kind = INSUFFICIENT_EVIDENCE
        selection = Selection(
            selection.fit,
            selection.evidence,
            selection.addressable,
            False,
            "evidence has no source",
            kind,
        )
    return Qualification(
        evidence_class=kind,
        accepted=selection.accepted and kind != INSUFFICIENT_EVIDENCE,
        why=selection.why,
        fit=selection.fit,
        evidence=selection.evidence,
        ambiguous=bool(_AMBIGUOUS.search(text or "")),
    )


def confirm_inferred(text: str, qualification: Qualification, completion) -> Qualification:
    """Cheap model only for an inferred or mixed page. Explicit intent is not re-sent.

    The model's quote must be a verbatim substring. Otherwise the deterministic
    class stands. `completion` None leaves the deterministic result untouched.
    """

    if completion is None or qualification.evidence_class == EXPLICIT_INTENT:
        return qualification
    if qualification.evidence_class not in (INFERRED_OPPORTUNITY, ICP_MATCH) and not qualification.ambiguous:
        return qualification
    raw = completion.complete(system=_CONFIRM_SYSTEM, user=(text or "")[:1200])
    payload = _parse(raw)
    if payload is None:
        return qualification
    kind = str(payload.get("evidence_class") or "")
    quote = str(payload.get("evidence_quote") or "")
    if kind not in CLASSES or (quote and quote not in (text or "")):
        return qualification
    if kind == EXPLICIT_INTENT and not quote:
        return qualification
    accepted = qualification.accepted and kind in (EXPLICIT_INTENT, INFERRED_OPPORTUNITY, ICP_MATCH)
    if kind in (INSUFFICIENT_EVIDENCE, IRRELEVANT):
        accepted = False
    why = qualification.why
    if not accepted and not why:
        why = "cheap classifier did not confirm purchase intent"
    return Qualification(
        evidence_class=kind,
        accepted=accepted,
        why=why,
        fit=qualification.fit,
        evidence=qualification.evidence,
        ambiguous=False,
    )


def confirm_batch(items: list[tuple[str, Qualification]], completion) -> list[Qualification]:
    """One call for many inferred rows. A bad item keeps its deterministic class.

    The engine does not use this on the live path: one malformed row must not
    blank the run. The evaluation harness does.
    """

    if completion is None or not items:
        return [pair[1] for pair in items]
    pending = [
        (index, text, qual)
        for index, (text, qual) in enumerate(items)
        if qual.evidence_class == INFERRED_OPPORTUNITY or qual.ambiguous
    ]
    if not pending:
        return [pair[1] for pair in items]
    lines = [f"[{index}] {(text or '')[:400]}" for index, text, _qual in pending]
    raw = completion.complete(
        system=_CONFIRM_SYSTEM + "\nReturn a JSON array with one object per numbered item, including \"index\".",
        user="\n".join(lines)[:4000],
    )
    payload = _parse(raw)
    by_index: dict[int, dict] = {}
    rows = payload if isinstance(payload, list) else None
    if rows is None and isinstance(payload, dict):
        rows = payload.get("items") if isinstance(payload.get("items"), list) else None
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("index"), int):
                by_index[row["index"]] = row
    out = [pair[1] for pair in items]
    for index, text, qual in pending:
        row = by_index.get(index)
        if row is None:
            continue
        out[index] = confirm_inferred(text, qual, _DictCompletion(row))
    return out


class _DictCompletion:
    """Adapts one already-parsed batch row to `confirm_inferred`'s completion port."""

    def __init__(self, row: dict) -> None:
        self._row = row

    def complete(self, *, system: str, user: str) -> str:
        del system, user
        return json.dumps(self._row)


def _parse(raw: str):
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
