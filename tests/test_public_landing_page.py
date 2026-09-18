from __future__ import annotations

from core.public_landing_page import render_public_landing_page_html


def test_public_landing_page_explains_the_one_lane_story() -> None:
    html = render_public_landing_page_html()

    assert "VOOL · Local-first agent runtime" in html
    assert "Run it locally. Check the work. Verify the proof." in html
    assert "One system. One lane." in html
    assert "Current lane" in html
    assert "/api/dashboard" in html
    assert "Get VOOL" in html
    assert 'rel="canonical" href="https://voolbook.com/"' in html
    assert 'href="/feed"' in html
    assert 'href="/hive"' in html
    assert 'href="/status"' in html
    assert "What VOOL Is" in html


def test_public_landing_page_exposes_the_full_public_route_index() -> None:
    html = render_public_landing_page_html()

    assert "Public routes" in html
    assert "Browse Order" in html
    assert 'id="public-routes"' in html
    for href, label, description in (
        ("/proof", "Proof", "finalized results and the receipts that justify them"),
        ("/tasks", "Tasks", "open and finished work with status, owner, and evidence links"),
        ("/agents", "Operators", "who is doing the work, what they are handling now, and what they have actually closed"),
        ("/feed", "Worklog", "public work notes and research updates tied back to tasks, operators, and proof"),
        ("/status", "Status", "what already works, what is rough, and what is still not ready"),
        ("/hive", "Coordination", "read-only shared task state for work that moves beyond one runtime"),
    ):
        assert f'href="{href}"' in html
        assert label in html
        assert description in html


def test_public_landing_page_uses_route_first_top_navigation() -> None:
    html = render_public_landing_page_html()

    nav_start = html.index('<nav class="ns-nav" aria-label="Primary">')
    nav_end = html.index("</nav>", nav_start)
    nav_html = html[nav_start:nav_end]

    assert ">Proof<" in nav_html
    assert ">Tasks<" in nav_html
    assert ">Operators<" in nav_html
    assert ">Worklog<" in nav_html
    assert ">Status<" in nav_html
    assert ">Coordination<" in nav_html
    assert ">Home<" not in nav_html
    assert nav_html.index(">Proof<") < nav_html.index(">Tasks<") < nav_html.index(">Operators<") < nav_html.index(">Worklog<") < nav_html.index(">Status<") < nav_html.index(">Coordination<")


def test_public_landing_page_drops_atmospheric_ai_background_treatment() -> None:
    html = render_public_landing_page_html()

    assert "background: var(--bg);" in html
    assert "backdrop-filter: blur" not in html
    assert "nl-hero-main::before" not in html
    assert "linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px)" not in html


def test_public_landing_page_demotes_economy_language_from_the_hero_strip() -> None:
    html = render_public_landing_page_html()

    assert "Solved tasks" in html
    assert "Released credits" not in html
