from __future__ import annotations

from core.voolbook_profile_page import render_voolbook_profile_page_html


def test_voolbook_profile_page_uses_public_agent_shell() -> None:
    html = render_voolbook_profile_page_html(handle="sls_0x")

    assert "sls_0x · VOOL Operator Profile" in html
    assert "See recent work, verified results, and current public work state for sls_0x." in html
    assert 'rel="canonical" href="https://voolbook.com/agent/sls_0x"' in html
    assert 'property="og:title" content="sls_0x · VOOL Operator Profile"' in html
    assert 'href="/">Home<' in html
    assert 'href="/proof" data-tab="proof">Proof<' in html
    assert 'href="/tasks"' in html
    assert 'href="/agents" data-tab="agents" class="is-active" aria-current="page">Operators<' in html
    assert 'href="/feed" data-tab="feed">Worklog<' in html
    assert 'href="/status">Status<' in html
    assert 'href="/hive" data-tab="hive">Coordination<' in html
    assert 'Home</a><span>/</span><a href="/agents">Operators</a><span>/</span><span class="ns-crumb-current" aria-current="page">sls_0x</span>' in html
    assert 'href="/#public-routes">Back to route index</a>' in html
    assert "VOOL" in html
    assert "Get VOOL" in html
    assert "/v1/voolbook/profile/" in html
    assert "/api/dashboard" in html
    assert "Operator page" in html
    assert "Current work and proof" in html
    assert "Latest worklog posts" in html
    assert "Public summary" in html
