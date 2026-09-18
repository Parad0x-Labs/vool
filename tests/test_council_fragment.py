"""Council convene fragment — mount, namespace, and truth-surface laws.

String-level truths about the one document the server serves, plus a node boot of the
full page proving the fragment evaluates and freezes its namespace without a browser.
"""

from __future__ import annotations

import re

from core.council_fragment import render_council_fragment
from core.vool_chat_page import _VOOL_CHAT_HTML, render_vool_chat_html


def test_fragment_is_mounted_after_settings_without_developer_scene_lab() -> None:
    page = render_vool_chat_html(build_commit="test-commit")
    council_at = page.find("window.VoolCouncilConvene")
    settings_at = page.find("window.VoolSettingsExtras")
    assert council_at != -1, "council fragment missing from the rendered page"
    assert settings_at != -1
    assert settings_at < council_at
    assert "window.VoolSceneLab" not in page


def test_namespace_and_prefix_are_single_owner() -> None:
    fragment = render_council_fragment()
    for assignment in re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", fragment):
        assert assignment.startswith("Vool"), f"fragment leaks window.{assignment}"
    assert "vcc" not in _VOOL_CHAT_HTML, "the monolith must not use the fragment's vcc prefix"


def test_surface_states_the_council_laws_verbatim() -> None:
    fragment = render_council_fragment()
    assert "roles, not personalities" in fragment
    assert "counterexample beats" in fragment
    assert "promotion, merge and spend stay with you" in fragment
    assert "blind round 1" in fragment, "the exhibits field must state the blindness contract"


def test_roles_offered_match_the_server_registry_exactly() -> None:
    from core.council.roles import ROLE_REGISTRY

    fragment = render_council_fragment()
    offered = set(re.findall(r"\['([a-z_]+)','[A-Z]", fragment))
    assert offered == set(ROLE_REGISTRY.keys()), (
        "the picker's role list must be exactly the server registry — no phantom roles, none missing"
    )


def test_fragment_talks_only_to_real_endpoints() -> None:
    fragment = render_council_fragment()
    called = set(re.findall(r"fetch\('([^']+)'", fragment)) | set(
        re.findall(r"fetch\('([^']+)\?' ", fragment)
    )
    called |= set(re.findall(r"fetch\('([^?']+)\?", fragment))
    expected = {
        "/api/council/convene",
        "/api/council/stop",
        "/api/council/runs",
        "/api/council/status",
        "/api/cloud/models",
    }
    assert {c.split("?")[0] for c in called} == expected


def test_no_fake_progress_working_is_gated_on_live_open_round() -> None:
    fragment = render_council_fragment()
    assert "live && run.state === 'round_open'" in fragment, (
        "a WORKING chip may render only while the server says the round is open and the run "
        "is live — anything else is invented activity"
    )


def test_poll_survives_a_not_ok_status_instead_of_dying_silently() -> None:
    """The dead-Convene bug (live, 2026-08-28): the first status poll raced the state file,
    got a 404, and the poller returned without rescheduling — the run went on invisibly."""
    fragment = render_council_fragment()
    assert "if (!data.ok) { pollTimer = setTimeout(" in fragment


def test_nerd_stats_fold_into_a_collapsed_drawer_below_the_convene_surface() -> None:
    fragment = render_council_fragment()
    assert "Technical details" in fragment
    assert "nerd.hidden = true;" in fragment, "the drawer starts collapsed for normal users"
    assert "bodyEl.insertBefore(rootEl, bodyEl.firstChild)" in fragment, (
        "the convene surface mounts FIRST; the truth room folds below it"
    )


def test_page_boots_under_node_and_namespace_is_frozen() -> None:
    from tests.chat_page_js_harness import DOM, run_node

    page = render_vool_chat_html(build_commit="node-boot")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.DOTALL)
    program = (
        DOM
        + "\n;(function(){\n"
        + "\n;\n".join(scripts)
        + "\nvar ns = window.VoolCouncilConvene;"
        + "\nvar frozen = false; try { ns.extra = 1; frozen = ns.extra !== 1; } catch (e) { frozen = true; }"
        + "\nout({ ns: typeof ns, frozen: frozen,"
        + " seats: ns ? ns.seats() : null, active: ns ? ns.activeRun() : 'missing' });\n})();\n"
    )
    result = run_node(program)
    assert result["ns"] == "object"
    assert result["frozen"] is True, "the namespace must be frozen like every other fragment"
    assert result["active"] is None, "no run may be claimed active before one is convened"
    assert result["seats"] == [], (
        "seats stay empty until the overlay first opens — the default bench appears on mount"
    )


# ------------------------------------------------- companion drawer (ux-pass1 exactness)


def test_pet_appearance_uses_settings_without_header_clutter() -> None:
    from core.companion_drawer_fragment import render_companion_drawer_fragment

    fragment = render_companion_drawer_fragment()
    page = render_vool_chat_html(build_commit="test-commit")
    assert "window.VoolCompanionDrawer" in page
    assert "Pet appearance" in fragment
    for removed in ("vcdCapBtn", "vcdPalBtn", "vcdOpenLab", "vool_profile_prefs", "voiceTab", "profileTab"):
        assert removed not in fragment
    assert "drawer.hidden = true" in fragment


def test_drawer_retains_actual_pet_controls() -> None:
    from core.companion_drawer_fragment import render_companion_drawer_fragment

    fragment = render_companion_drawer_fragment()
    assert "Your tasks and their status stay the same" in fragment
    for action in ("chooseCharacter", "choosePack", "companion.hideDesktopPet()", "companion.hide(false)", "companion.dock()"):
        assert action in fragment
    for assignment in re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", fragment):
        assert assignment.startswith("Vool"), f"drawer leaks window.{assignment}"


def test_shortcut_map_is_fully_visible() -> None:
    from core.command_palette_fragment import render_palette_fragment
    from core.companion_drawer_fragment import render_companion_drawer_fragment
    from core.composer_extras_fragment import render_composer_extras_fragment

    palette = render_palette_fragment()
    for chip in ("⌘K", "⌘N", "⌘J", "⌘/", "⌘F", "⌘,", "Esc"):
        assert chip in palette, f"hint bar missing {chip}"
    drawer = render_companion_drawer_fragment()
    assert "u2318N" in drawer, "the New chat button gains its \\u2318N kbd chip"
    extras = render_composer_extras_fragment()
    assert "Search chats" in extras and "u2318F" in extras, "sidebar search carries the \\u2318F hint"
    assert "sessions.parentNode.appendChild(wrap)" in extras, "search sits at the sidebar bottom"
    assert "margin-top:auto" in extras


def test_companion_popover_is_placed_toggled_and_closable() -> None:
    """Live defect 2026-08-28: a tap opened the provenance popover unpositioned (top-left),
    every tap reopened it, and nothing but its own X could close it."""
    from core.companion_presentation_fragment import render_companion_fragment

    fragment = render_companion_fragment()
    assert 'vnPop.style.left = Math.round(px) + "px"' in fragment, "popover must be placed by the sprite"
    assert 'if (vnPop && vnPop.style.display === "block") { vnPop.style.display = "none"; return; }' in fragment, (
        "a tap on the sprite toggles the popover instead of reopening it"
    )
    assert '-webkit-tap-highlight-color: transparent' in fragment, "WKWebView tap square must be killed"
    assert '.vn-caption:empty, #companionLayer .vn-menu:empty { display: none; }' in fragment
    assert 'e.key === "Escape" && vnPop' in fragment, "Esc closes the popover"
    assert "atEdge && vnNativeCompanionApi()" in fragment, "edge-release hands the sprite to the desktop"


def test_drawer_pet_tab_offers_desktop_detach() -> None:
    from core.companion_drawer_fragment import render_companion_drawer_fragment

    fragment = render_companion_drawer_fragment()
    assert "vcdDetach" in fragment and "Move to desktop" in fragment
    assert "companion.detachDesktop" in fragment


def test_drag_gesture_listeners_are_one_shot() -> None:
    """Live defect 2026-08-28 #2: the drag pipeline's window pointerup listener was never
    removed, so after one touch EVERY click anywhere read as a sprite tap and opened the
    status card. The gesture must detach its own listeners and a stale fire must no-op."""
    from core.companion_presentation_fragment import render_companion_fragment

    fragment = render_companion_fragment()
    assert 'window.removeEventListener("pointerup", up)' in fragment
    assert 'window.removeEventListener("pointermove", move)' in fragment
    assert 'window.removeEventListener("pointercancel", cancel)' in fragment
    detach_then_guard = fragment.find("detach();\n      if (!vnDrag) return;")
    assert detach_then_guard != -1, "a stale pointerup must detach and do NOTHING (no popover)"
