"""Normalize and deduplicate search hits before anyone fetches them."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from evorove_lead.page_extract import ExtractedPage


def normalize_url(url: str) -> str:
    """Drop fragments and default ports. Keep the first-seen original URL elsewhere."""

    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return raw
    host = parts.hostname.casefold()
    port = parts.port
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def dedupe_hits(hits):
    """Keep the first hit for each normalized URL. The stored URL is the original one."""

    seen: set[str] = set()
    kept = []
    for hit in hits:
        key = normalize_url(hit.url)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(hit)
    return tuple(kept)


def dedupe_pages(pages: tuple[ExtractedPage, ...] | list[ExtractedPage]) -> tuple[ExtractedPage, ...]:
    seen_urls: set[str] = set()
    seen_text: set[str] = set()
    kept: list[ExtractedPage] = []
    for page in pages:
        url_key = normalize_url(page.canonical_url or page.source_url)
        text_key = page.content_sha256
        if url_key in seen_urls or text_key in seen_text:
            continue
        seen_urls.add(url_key)
        seen_text.add(text_key)
        kept.append(page)
    return tuple(kept)
