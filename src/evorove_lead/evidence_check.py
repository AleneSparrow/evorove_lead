"""Check that a classification's evidence is actually in the source text.

A quote the model wrote that does not appear in the extracted text is
not evidence. A publication date the extractor did not find is not a date.
"""

from __future__ import annotations

BUYER_MARKERS = (
    "looking for",
    "i need",
    "we need",
    "recommend a",
    "can anyone",
    "searching for",
    "hiring",
    "who can help",
)
SELLER_MARKERS = (
    "we offer",
    "our services",
    "we provide",
    "book us",
    "our pricing",
    "for sale",
)

INTENT_CLASSES = (
    "EXPLICIT_INTENT",
    "INFERRED_OPPORTUNITY",
    "ICP_MATCH",
    "INSUFFICIENT_EVIDENCE",
    "IRRELEVANT",
)


def quote_in_source(quote: str | None, source_text: str) -> bool:
    if not quote or not quote.strip():
        return False
    folded_quote = " ".join(quote.split()).casefold()
    folded_source = " ".join((source_text or "").split()).casefold()
    return folded_quote in folded_source


def looks_like_competitor(text: str, service: str) -> bool:
    """True when the page is selling the same service and nobody is asking for it."""

    folded = (text or "").casefold()
    if any(marker in folded for marker in BUYER_MARKERS):
        return False
    if not any(marker in folded for marker in SELLER_MARKERS):
        return False
    tokens = [word.casefold() for word in service.split() if len(word) >= 4]
    if not tokens:
        return False
    return any(token in folded for token in tokens)


def segment_for(intent_class: str, evidence_verified: bool) -> str:
    if intent_class == "EXPLICIT_INTENT" and evidence_verified:
        return "opportunity"
    if intent_class == "INFERRED_OPPORTUNITY" and evidence_verified:
        return "opportunity"
    if intent_class == "ICP_MATCH":
        return "prospect"
    if intent_class == "INSUFFICIENT_EVIDENCE":
        return "review"
    return "rejected"
