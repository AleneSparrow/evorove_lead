"""Headless-render a page whose content only exists after JS runs.

Fallback only. `presence.py` tries a plain HTTP fetch first -- it is
faster and needs no browser -- and only reaches here when that fetch
came back with almost no visible text (a client-rendered SPA serves an
empty `<div id="root"></div>` shell over plain HTTP; `evorove.com`
itself is one). The caller is responsible for the SSRF/public-host check
before ever handing this a URL -- this module does not repeat it.

Playwright (and its Chromium download, `python -m playwright install
chromium`) is an optional, heavier dependency than the rest of this
repo's stdlib-only fetchers. It is imported lazily, right where it's
used, so nothing here breaks for a caller who never needs the fallback.
"""

from __future__ import annotations

from evorove_lead.presence import USER_AGENT, RenderRejected

RENDER_TIMEOUT_MS = 15_000


def render_page_html(url: str, *, timeout_ms: int = RENDER_TIMEOUT_MS) -> str:
    """Load `url` in headless Chromium and return the rendered HTML.

    Raises `RenderRejected` on any failure -- missing Playwright install,
    launch failure, navigation timeout -- so the caller can fall back to
    whatever the plain fetch already got instead of raising past it.
    """

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
                page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise RenderRejected(f"page could not be rendered: {exc}") from exc
