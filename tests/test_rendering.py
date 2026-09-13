"""render_page_html against a real headless Chromium (playwright).

Skipped automatically if Playwright/Chromium isn't installed in this
environment -- the render fallback in presence.py is best-effort by
design, and this module is optional infrastructure on top of it.
"""

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")

from evorove_lead.presence import RenderRejected
from evorove_lead.rendering import render_page_html


def test_renders_real_javascript_generated_content(tmp_path):
    page_file = tmp_path / "spa.html"
    page_file.write_text(
        "<html><head><title>Test SPA</title></head><body>"
        "<div id='root'></div>"
        "<script>document.getElementById('root').innerHTML = "
        "'<h1>Rendered by JS</h1><p>This text only exists after script runs.</p>';</script>"
        "</body></html>",
        encoding="utf-8",
    )

    html = render_page_html(page_file.as_uri())

    assert "Rendered by JS" in html
    assert "This text only exists after script runs." in html


def test_raises_render_rejected_on_navigation_failure():
    with pytest.raises(RenderRejected):
        render_page_html("http://127.0.0.1:1/definitely-not-listening", timeout_ms=3000)
