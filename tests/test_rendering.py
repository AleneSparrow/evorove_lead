"""Playwright fallback must use the same public-host rule as evidence fetch.

A local `file://` SPA is no longer a valid render target: Chromium must not
be asked to open private, loopback, metadata, or non-http(s) addresses.
JavaScript-shell fallback is covered by `tests/test_presence_render_fallback.py`
with an injected renderer.
"""

import pytest

from evorove_lead.rendering import RenderRejected, render_page_html


def test_render_rejects_file_and_loopback_without_launching_browser():
    with pytest.raises(RenderRejected):
        render_page_html("file:///tmp/spa.html")
    with pytest.raises(RenderRejected):
        render_page_html("http://127.0.0.1:1/definitely-not-listening", timeout_ms=3000)


def test_render_rejects_metadata_url():
    with pytest.raises(RenderRejected):
        render_page_html("http://169.254.169.254/latest/meta-data")
