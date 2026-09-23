"""The fragment mount and the chip vocabulary — U1 of the UX integration pass.

Deterministic environmental assertions on the RENDERED page: the fragments are present, in
order, namespaced, and collision-free against the monolith. No browser needed — these are
string-level truths about the one document the server actually serves.
"""

from __future__ import annotations

import re

from core.ui_chip_fragment import render_chip_fragment
from core.vool_chat_page import _VOOL_CHAT_HTML, render_vool_chat_html


def test_rendered_page_mounts_every_fragment_in_order() -> None:
    page = render_vool_chat_html(build_commit="test-commit")
    chips_at = page.find("window.VoolChips")
    # `.vool-ninja` is the companion FRAGMENT's own CSS — the monolith mentions the
    # `VoolCompanion` namespace at its call site, so the namespace string cannot mark order.
    companion_fragment_at = page.find("function vnBoot")
    assert chips_at != -1, "chip fragment missing from the rendered page"
    assert companion_fragment_at != -1, "companion fragment missing from the rendered page"
    assert chips_at < companion_fragment_at, "chips must evaluate before the companion layer"
    assert page.rstrip().endswith("</html>")
    # Both fragments sit before the single body close (self-contained document).
    assert page.count("</body>") == 1
    assert chips_at < page.rfind("</body>")


def test_chip_vocabulary_carries_every_typed_state() -> None:
    fragment = render_chip_fragment()
    for state in (
        "running", "done", "pass", "partial", "failed",
        "refused", "unknown", "waiting", "approval", "cancelled",
    ):
        assert f".vst-{state}" in fragment, f"state {state} has no chip class"
    # Colour is never the only signal: the two not-a-result states carry a dashed border.
    refused_rule = re.search(r"\.vst-refused\{[^}]*\}", fragment)
    unknown_rule = re.search(r"\.vst-unknown\{[^}]*\}", fragment)
    assert refused_rule and "dashed" in refused_rule.group(0)
    assert unknown_rule and "dashed" in unknown_rule.group(0)
    # UNKNOWN is never success-green.
    assert unknown_rule and "#4ade80" not in unknown_rule.group(0)
    # WAITING respects reduced motion.
    assert "prefers-reduced-motion" in fragment


def test_chip_fragment_escapes_labels_and_defaults_unknown() -> None:
    fragment = render_chip_fragment()
    # The escaper exists and is applied inside chipHtml — a label cannot inject markup.
    assert "replace(/</g,'&lt;')" in fragment
    assert "hasOwnProperty" in fragment  # an unrecognised state falls back to 'unknown'


def test_vst_prefix_is_reserved_to_the_fragment() -> None:
    """The monolith must not use the fragment's class prefix — that collision restyles real UI.

    (The prototype's `.st` prefix was already taken by the served page as a muted sub-text
    class; `vst-` exists precisely because of that. Keep it single-owner.)
    """
    assert "vst-" not in _VOOL_CHAT_HTML
    assert ".vst" not in _VOOL_CHAT_HTML


def test_fragments_expose_only_vool_namespaces() -> None:
    """A fragment writes `window.Vool*` and nothing else onto window (companion precedent)."""
    from core.command_palette_fragment import render_palette_fragment

    for fragment in (render_chip_fragment(), render_palette_fragment()):
        for assignment in re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", fragment):
            assert assignment.startswith("Vool"), f"fragment leaks window.{assignment}"


# ------------------------------------------------------------------ U2: palette + Esc cascade


def test_palette_fragment_mounts_between_chips_and_companion() -> None:
    page = render_vool_chat_html(build_commit="test-commit")
    chips_at = page.find("window.VoolChips")
    palette_at = page.find("window.VoolPalette")
    companion_fragment_at = page.find("function vnBoot")
    assert -1 < chips_at < palette_at < companion_fragment_at


def test_vp_prefix_is_reserved_to_the_palette_fragment() -> None:
    assert "vp-" not in _VOOL_CHAT_HTML
    assert "vpOverlay" not in _VOOL_CHAT_HTML


def test_page_actions_adapter_is_the_single_fragment_surface() -> None:
    """Fragments drive the page ONLY through the frozen VoolPageActions adapter."""
    assert _VOOL_CHAT_HTML.count("window.VoolPageActions = Object.freeze") == 1
    for action in (
        "newChat", "togglePanel", "openSettings", "openModelMenu", "openModeMenu",
        "focusComposer", "toast", "hasLiveRun", "stopDisplayedRun", "liveStopControl",
    ):
        assert action in _VOOL_CHAT_HTML.split("window.VoolPageActions")[1][:2200], (
            f"adapter is missing {action}"
        )


def test_esc_cascade_uses_the_existing_cancel_path_only() -> None:
    """Esc stops a run through the run's OWN Stop control — never a second cancel client."""
    from core.command_palette_fragment import render_palette_fragment

    fragment = render_palette_fragment()
    assert "stopDisplayedRun" in fragment
    assert "/api/chat/cancel" not in fragment, "the fragment must not reimplement cancel"
    # Capture-phase snapshot exists (third argument true) and the bubble arm defers to it.
    assert re.search(r"addEventListener\('keydown',[\s\S]+?\}, true\)", fragment)
    assert "escSawOpenSurface" in fragment
    # Global shortcuts require a modifier; a bare key is never stolen from the composer.
    assert "var combo = ev.metaKey || ev.ctrlKey;" in fragment
    assert "if (!combo) return;" in fragment


def test_palette_carries_no_prototype_demo_actions() -> None:
    """The design authority's simulated rows must not ship (no fake tasks, no fake refusals)."""
    from core.command_palette_fragment import render_palette_fragment

    fragment = render_palette_fragment()
    for banned in ("demo task", "Simulate", "Fix this PR", "startTaskFlow"):
        assert banned not in fragment


# ------------------------------------------------------------------ U3: composer extras


def test_extras_fragment_mounts_before_the_companion() -> None:
    page = render_vool_chat_html(build_commit="test-commit")
    palette_at = page.find("window.VoolPalette")
    # The namespace name also appears at the monolith's call site; the fragment's own CSS id and
    # the freeze assignment are unique to the fragment.
    extras_at = page.find("#vxFilterWrap")
    extras_assign_at = page.find("window.VoolComposerExtras = Object.freeze")
    companion_fragment_at = page.find("function vnBoot")
    assert -1 < palette_at < extras_at < companion_fragment_at
    assert palette_at < extras_assign_at < companion_fragment_at


def test_vx_prefix_and_switch_seam_are_single_owner() -> None:
    assert "vx-" not in _VOOL_CHAT_HTML
    assert _VOOL_CHAT_HTML.count("VoolComposerExtras.chatSwitched") == 1, (
        "exactly one named switch call site — setDisplayedChat"
    )
    # The call site fires only on a REAL change and is guarded against an unmounted fragment.
    site = _VOOL_CHAT_HTML.split("VoolComposerExtras.chatSwitched")[0][-400:]
    assert "previousChat !== displayedChat" in site


def test_drafts_never_carry_across_chats() -> None:
    from core.composer_extras_fragment import render_composer_extras_fragment

    fragment = render_composer_extras_fragment()
    switch_body = fragment.split("function chatSwitched")[1].split("function ")[0]
    # The previous chat's draft is SAVED before the next chat's draft is read — order is the law.
    assert switch_body.index("writeDraft(previousChat") < switch_body.index("readDraft(nextChat")
    # Storage is always try/caught: a private window degrades to no drafts, never a broken composer.
    read_body = fragment.split("function readDraft")[1].split("function ")[0]
    write_body = fragment.split("function writeDraft")[1].split("function ")[0]
    assert "try {" in read_body and "catch" in read_body
    assert "try {" in write_body and "catch" in write_body


def test_composer_has_no_duplicate_voice_placeholder_or_speech_apis() -> None:
    from core.composer_extras_fragment import render_composer_extras_fragment

    fragment = render_composer_extras_fragment()
    assert "vxMicBtn" not in fragment
    for banned in ("getUserMedia", "MediaRecorder", "SpeechRecognition", "speechSynthesis"):
        assert banned not in fragment, f"the disabled voice shell must not touch {banned}"
    # And no fake-listening theater: no interval/timeout drives any 'listening' presentation.
    assert "Listening" not in fragment


def test_emotes_use_only_the_real_companion_renderer() -> None:
    from core.composer_extras_fragment import render_composer_extras_fragment

    fragment = render_composer_extras_fragment()
    assert "VoolCompanionWorld" in fragment
    # No second art system: no embedded sprite rows / pixel strings in this fragment.
    assert "VN_SHEETS" not in fragment and "vcwDude" not in fragment
    # Token vocabulary is closed and the renderer refuses to run before the world exists.
    assert "EMOTE_STATES" in fragment
    assert "if (!world()) return;" in fragment


# ------------------------------------------------------------------ U4: price-safety gate


def test_price_gate_mounts_and_prefix_is_reserved() -> None:
    page = render_vool_chat_html(build_commit="test-commit")
    extras_at = page.find("#vxFilterWrap")
    gate_at = page.find("window.VoolPriceGate = Object.freeze")
    companion_fragment_at = page.find("function vnBoot")
    assert -1 < extras_at < gate_at < companion_fragment_at
    # The page reuses the gate button class; its CSS and overlay remain fragment-owned.
    assert ".vg-" not in _VOOL_CHAT_HTML
    assert "vgOverlay" not in _VOOL_CHAT_HTML


def test_every_live_confirm_site_uses_the_gate_with_confirm_fallback() -> None:
    """Server-classified pin and per-turn decisions keep their unmounted fallback.
    Model rows must not add another confirmation before the server-owned pin decision.
    """
    assert _VOOL_CHAT_HTML.count("await window.VoolPriceGate.review") == 2
    assert _VOOL_CHAT_HTML.count("window.confirm") == 2
    assert "ensurePaidPinAck" not in _VOOL_CHAT_HTML


def test_gate_never_classifies_cost_and_never_dispatches() -> None:
    """The server stays the sole cost authority: the fragment carries no confirm_paid, never
    POSTs the pin endpoint, and only READS the public catalog for rates/freshness."""
    from core.price_safety_fragment import render_price_safety_fragment

    fragment = render_price_safety_fragment()
    assert "confirm_paid" not in fragment
    assert "/api/cloud/model'" not in fragment and '/api/cloud/model"' not in fragment
    assert "/api/cloud/models?order=" in fragment
    assert "Your spending limits still apply" in fragment
    # The refresh-before-paid-dispatch law and the stale marker.
    assert "refresh=1" in fragment and "STALE" in fragment
    # Rates render only from real catalog fields; cache/reasoning rows are conditional.
    assert "prompt_usd_per_m" in fragment and "completion_usd_per_m" in fragment
    assert "cache_read_usd_per_m != null" in fragment
    # Escape resolves the decision to FALSE — never an implicit confirm.
    assert "closeGate(false)" in fragment


def test_palette_boots_under_node_with_real_action_registry() -> None:
    """Boot the real page + fragments under node; the palette registry must hold real actions."""
    from tests.chat_page_js_harness import DOM, run_node

    page = render_vool_chat_html(build_commit="node-boot")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.DOTALL)
    program = (
        DOM
        + "\n;(function(){\n"
        + "\n;\n".join(scripts)
        + "\nout({ actions: window.VoolPalette ? window.VoolPalette.actions() : null,"
        + " adapter: typeof window.VoolPageActions,"
        + " extras: typeof window.VoolComposerExtras,"
        + " gate: typeof window.VoolPriceGate,"
        + " chips: window.VoolChips ? window.VoolChips.states : null });\n})();\n"
    )
    result = run_node(program)
    assert result["adapter"] == "object"
    assert result["extras"] == "object"
    assert result["gate"] == "object"
    assert result["chips"] and "unknown" in result["chips"]
    actions = result["actions"]
    assert actions, "palette registered no actions"
    for expected in ("new-chat", "toggle-activity", "model-menu", "settings", "companion-lab"):
        assert expected in actions


# ------------------------------------------------------------------ U5: sprite recovery + scenes


def test_sprite_sheets_live_in_the_world_as_a_single_embed() -> None:
    """The 126-frame Prism/Veil/Ember art ships ONCE, in the world module both windows load."""
    import core.companion_presentation_fragment as presentation
    from core.companion_world_fragment import COMPANION_WORLD_JS, render_desktop_companion_html

    page = render_vool_chat_html(build_commit="test-commit")
    assert page.count("VCW_SHEETS = {") == 1, "sheet data must be embedded exactly once"
    assert "VCW_SHEETS" in COMPANION_WORLD_JS
    source = open(presentation.__file__.replace(".pyc", ".py")).read()
    assert "_VN_SPRITE_DATA" not in source, "the presentation module must not carry a second copy"
    desktop = render_desktop_companion_html({"character": "prism", "state": "thinking"})
    assert "VCW_SHEETS" in desktop and '"character":"prism"' in desktop


def test_recovered_trio_registered_as_sheet_renderers_with_tone_law() -> None:
    from core.companion_world_fragment import COMPANION_WORLD_JS

    for character in ("prism", "veil", "ember"):
        assert f"VCW_CHARACTERS.{character}=" in COMPANION_WORLD_JS.replace(" ", "")
    assert "renderer:'sheet'" in COMPANION_WORLD_JS
    # The tone-rim law: palette slot "r" is overridden by the typed tone, hex or VN_TONES lookup.
    assert "pal.r=tone" in COMPANION_WORLD_JS.replace(" ", "")
    assert "toneHex" in COMPANION_WORLD_JS
    # 2x rasterisation into the shared 48 canvas.
    assert "fillRect(x*2,y*2,2,2)" in COMPANION_WORLD_JS.replace(" ", "")


def test_companion_payload_allowlist_covers_the_recovered_family() -> None:
    from core.companion_world_fragment import normalise_companion_payload

    for character in ("spark", "rascal", "prime", "prism", "veil", "ember"):
        assert normalise_companion_payload({"character": character})["character"] == character
    assert normalise_companion_payload({"character": "impostor"})["character"] == "spark"


def test_worker_scenes_bind_only_to_the_ledger_derivation() -> None:
    """ONE monolith call site, fed from the same agentRowsFrom fold the Agents tab trusts."""
    assert _VOOL_CHAT_HTML.count("window.VoolCompanion.agents(") == 1
    site = _VOOL_CHAT_HTML.split("window.VoolCompanion.agents(")[0][-420:]
    assert "agentRowsFrom(owner.chatLedger" in site
    assert ".filter((row) => row.running)" in site
    from core.companion_presentation_fragment import render_companion_fragment

    fragment = render_companion_fragment()
    # The engine draws a scene only for live counts >= 2, in active states, with expiry.
    assert "VN_AGENTS_TTL_MS" in fragment
    assert "workers >= 2" in fragment
    assert "VN_SCENE_STATES" in fragment
    # Only the two real-count scenes exist; no choreographed fail-cluster/success-crowd ships.
    from core.companion_world_fragment import COMPANION_WORLD_JS

    assert "scene==='pair'" in COMPANION_WORLD_JS.replace(" ", "")
    assert "scene==='huddle'" in COMPANION_WORLD_JS.replace(" ", "")
    for banned in ("'review'", "'fail'", "'success')", "'approval')"):
        assert f"scene==={banned}" not in COMPANION_WORLD_JS.replace(" ", "")


def test_frame_counts_under_node_for_sheet_and_procedural() -> None:
    from tests.chat_page_js_harness import DOM, run_node

    page = render_vool_chat_html(build_commit="node-boot")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.DOTALL)
    program = (
        DOM
        + "\n;(function(){\n"
        + "\n;\n".join(scripts)
        + "\nvar W = window.VoolCompanionWorld;"
        + "\nout({ prismIdle: W.frameCount('prism','idle'),"
        + " prismUnknownState: W.frameCount('prism','never-a-state') > 0,"
        + " sparkIdle: W.frameCount('spark','idle'),"
        + " sparkSuccess: W.frameCount('spark','success'),"
        + " characters: Object.keys(W.characters).sort(),"
        + " agentsApi: typeof window.VoolCompanion.agents });\n})();\n"
    )
    result = run_node(program)
    assert result["prismIdle"] >= 2, "prism idle must expose its real sheet frame count"
    assert result["prismUnknownState"] is True, "unknown states fall back to the idle sheet"
    assert result["sparkIdle"] == 600 and result["sparkSuccess"] == 6
    assert result["characters"] == ["ember", "prime", "prism", "rascal", "spark", "veil"]
    assert result["agentsApi"] == "function"


# ------------------------------------------------------------------ U7: bell + scene lab + hint


def test_notification_fragment_is_truthful_and_mounted() -> None:
    from core.notification_fragment import render_notification_fragment

    page = render_vool_chat_html(build_commit="test-commit")
    fragment = render_notification_fragment()
    assert "vfBell" in page and "SCENE LAB" not in page
    # Exactly one lifecycle call site, reporting truth; the fragment decides visibility.
    assert _VOOL_CHAT_HTML.count("window.VoolNotify.runFinished({") == 1
    # Displayed-chat completions never notify (the operator watched the card end).
    assert "if (info.displayed) return;" in fragment
    # Market items come only from the typed server feed with a cursor; empties are stated.
    assert "/api/cloud/market-events?after=" in fragment
    assert "No notifications yet" in fragment
    # No invented rows: none of the prototype's hardcoded market items ship.
    for banned in ("Gemini Flash", "Ling 2 Mini", "Ling 3"):
        assert banned not in fragment


def test_scene_lab_is_dev_labelled_and_not_mounted_in_beta() -> None:
    import core.vool_chat_page as page_module
    from core.scene_lab_fragment import render_scene_lab_fragment

    fragment = render_scene_lab_fragment()
    assert ("SCENE LAB \\u00b7 DEV" in fragment) or ("SCENE LAB \u00b7 DEV" in fragment)
    assert "Removed before beta" in fragment
    # The puppet drives PRESENTATION only, through the engine's documented dev hook.
    assert "_devPuppet" in fragment
    for banned in ("agents(", "consume(", "/api/"):
        assert banned not in fragment, f"the lab must not touch {banned} — presentation only"
    # Developer source is retained, but the beta page must not mount its control.
    source = open(page_module.__file__.replace(".pyc", ".py")).read()
    assert "render_scene_lab_fragment()," not in source
    assert "window.VoolSceneLab" not in page_module.render_vool_chat_html()
    # And the engine caption exposes the puppet visibly while active.
    from core.companion_presentation_fragment import render_companion_fragment

    engine = render_companion_fragment()
    assert "DEV" in engine and "vnDevPuppet" in engine


def test_hint_bar_ships_with_the_shortcut_owner() -> None:
    from core.command_palette_fragment import render_palette_fragment

    fragment = render_palette_fragment()
    assert "vpHint" in fragment
    for key in ("\u2318K", "\u2318N", "\u2318J"):
        assert key in fragment, f"hint bar missing {key}"
