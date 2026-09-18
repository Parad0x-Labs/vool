from __future__ import annotations

from core.public_status_page import render_public_status_page_html


def test_public_status_page_is_a_real_public_destination() -> None:
    html = render_public_status_page_html()

    assert "VOOL Status" in html
    assert 'rel="canonical" href="https://voolbook.com/status"' in html
    assert 'href="/status" class="is-active" aria-current="page">Status<' in html
    assert "Working now" in html
    assert "Still hardening" in html
    assert "Not yet proven" in html
    assert "Open status doc" in html
    assert "Back to route index" in html
    assert 'Home</a><span>/</span><span class="ns-crumb-current" aria-current="page">Status</span>' in html
    assert "readable work state" in html
