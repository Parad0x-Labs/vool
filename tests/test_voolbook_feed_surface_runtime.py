from __future__ import annotations

from core.voolbook_feed_surface_runtime import render_voolbook_feed_surface_runtime


def _surface_copy() -> dict[str, dict[str, object]]:
    return {
        "feed": {
            "kicker": "Public worklog",
            "heroTitle": "Read the work, not the theater.",
            "heroBody": "Public worklogs point back to tasks and proof.",
            "heroChips": ["Work notes", "Proof"],
            "title": "Worklog",
            "subtitle": "Public work tied to proof.",
            "pageTitle": "VOOL Worklog",
            "pageDescription": "Read public work tied to proof.",
            "searchPlaceholder": "Search worklogs...",
        },
        "proof": {
            "kicker": "Verified work",
            "heroTitle": "Finalized work. Verifiable receipts.",
            "heroBody": "Proof first.",
            "heroChips": ["Receipts"],
            "title": "Proof",
            "subtitle": "Finalized work and receipts.",
            "pageTitle": "VOOL Proof",
            "pageDescription": "Review finalized work.",
            "searchPlaceholder": "Search receipts...",
        },
    }


def test_surface_runtime_injects_state_and_copy() -> None:
    runtime = render_voolbook_feed_surface_runtime(
        api_base="/edge",
        initial_tab="proof",
        initial_view="receipts",
        surface_copy=_surface_copy(),
    )

    assert "const API = '/edge' || '';" in runtime
    assert "let activeTab = 'proof' || 'feed';" in runtime
    assert "let activeView = 'receipts' || 'all';" in runtime
    assert 'const surfaceCopy = {"feed":{"kicker":"Public worklog"' in runtime
    assert '"proof":{"kicker":"Verified work"' in runtime


def test_surface_runtime_keeps_load_and_sidebar_seam() -> None:
    runtime = render_voolbook_feed_surface_runtime(surface_copy=_surface_copy())

    assert "function renderFeed()" in runtime
    assert "function updateHeroLedger(openCount, solvedCount, agentCount, proof)" in runtime
    assert "function updateSidebar(dashboard)" in runtime
    assert "async function loadAll()" in runtime
    assert "setInterval(loadAll, 45000);" in runtime
