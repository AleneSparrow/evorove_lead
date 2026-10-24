"""Headless-render a page whose content only exists after JS runs.

Fallback only. `presence.py` tries a plain HTTP fetch first -- it is
faster and needs no browser -- and only reaches here when that fetch
came back with almost no visible text (a client-rendered SPA serves an
empty `<div id="root"></div>` shell over plain HTTP; `evorove.com`
itself is one).

Every document, redirect, and subresource is checked with the same
public-host rule as the evidence fetcher. `file://`, loopback, private,
and metadata addresses are rejected before Chromium is asked to load
them. This is not a sandbox: an already-dispatched browser request
cannot be recalled, and aborting a hop is not exactly-once.
"""

from __future__ import annotations

from evorove_lead.presence import (
    USER_AGENT,
    PresenceRejected,
    RenderRejected,
    require_public_http_url,
)

RENDER_TIMEOUT_MS = 15_000


def guard_render_url(url: str) -> str:
    """Reject a URL Chromium must not be asked to open."""

    try:
        return require_public_http_url(url)
    except PresenceRejected as exc:
        raise RenderRejected(str(exc)) from exc


def _abort_private_request(route) -> None:
    try:
        require_public_http_url(route.request.url)
    except PresenceRejected:
        route.abort("blockedbyclient")
        return
    route.continue_()


def render_page_html(url: str, *, timeout_ms: int = RENDER_TIMEOUT_MS) -> str:
    """Load `url` in headless Chromium and return the rendered HTML.

    Raises `RenderRejected` on any failure -- missing Playwright install,
    launch failure, navigation timeout, or a private/metadata hop -- so
    the caller can fall back to whatever the plain fetch already got.
    """

    guard_render_url(url)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderRejected(
            "playwright is not installed (pip install playwright, then "
            "`python -m playwright install chromium`)"
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.route("**/*", _abort_private_request)
                page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                guard_render_url(page.url)
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise RenderRejected(f"page could not be rendered: {exc}") from exc
    except PresenceRejected as exc:
        raise RenderRejected(str(exc)) from exc
