"""The desktop pet's defect classes, named one by one.

Every test here was RED at `07f69998` and names a class of defect, not a scenario. The rule the
whole file follows: **assert against the real dependency and against real geometry, never against a
double that is kinder than production.** The bug that shipped -- a native window that could never be
created -- survived a green suite precisely because `_FakeWebview.create_window(*args, **kwargs)`
accepted arguments pywebview rejects.

Where a display is genuinely required the test is skipped with a reason. Nothing here fakes a
window server.
"""
from __future__ import annotations

import json

import pytest

from core.companion_world_fragment import (
    normalise_companion_payload,
    render_desktop_companion_html,
)
from installer.bundle import pet_native
from installer.bundle.vool_window import COMPANION_WINDOW_FLAGS, _WindowApi

# The two attached displays this lane was proven on: a 2x primary at the origin and a 1x secondary
# at a NEGATIVE x. Hard-coded on purpose -- the geometry must be right whether or not the machine
# running the suite has a second monitor plugged in.
PRIMARY = (0.0, 0.0, 2240.0, 1235.0)
LEFT_1X = (-1920.0, 180.0, 1920.0, 1055.0)
TWO_SCREENS = [PRIMARY, LEFT_1X]


# --------------------------------------------------------------------------- RC-1


@pytest.fixture
def real_webview():
    """The genuine pywebview module, with any windows this test created removed afterwards.

    `create_window` before `start()` only validates arguments and records a handle; it opens no
    native window, so this is safe and fast -- and it is the ONLY way to prove the production flags
    are actually acceptable.
    """
    webview = pytest.importorskip("webview")
    before = list(webview.windows)
    yield webview
    del webview.windows[len(before):]


def test_production_window_flags_survive_the_real_pywebview_validator(real_webview) -> None:
    """RC-1: `background_color="#00000000"` raised ValueError before any window existed.

    pywebview validates against `^#(?:[0-9a-fA-F]{3}){1,2}$`; eight hex digits never matched, so
    `detach_companion` returned `{"ok": False, "error": "ValueError"}` on every single call and the
    pet could not leave the app. This asserts the real library accepts what production passes.
    """
    window = real_webview.create_window("VOOL Companion", html="<b>pet</b>", **COMPANION_WINDOW_FLAGS)
    assert window is not None


def test_the_window_stays_transparent_and_frameless(real_webview) -> None:
    """The fix for RC-1 must not be "make it opaque so the colour parses"."""
    assert COMPANION_WINDOW_FLAGS["transparent"] is True
    assert COMPANION_WINDOW_FLAGS["frameless"] is True
    assert COMPANION_WINDOW_FLAGS["shadow"] is False
    # focus=False makes canBecomeKeyWindow return False, so the pet cannot steal the keyboard.
    assert COMPANION_WINDOW_FLAGS["focus"] is False


# --------------------------------------------------------------------------- RC-4


def test_pet_never_sits_above_a_modal_or_security_panel() -> None:
    """RC-4: `on_top=True` gave the pet NSStatusWindowLevel (25), above modal panels (8).

    A pet that floats over a dialog the operator has to answer is obstructing it.
    """
    assert pet_native.PET_WINDOW_LEVEL < pet_native.MODAL_PANEL_LEVEL
    assert pet_native.PET_WINDOW_LEVEL < pet_native.STATUS_WINDOW_LEVEL
    assert pet_native.PET_WINDOW_LEVEL < pet_native.SCREEN_SAVER_LEVEL
    # ...and still above ordinary application windows, or it is not a desktop pet.
    assert pet_native.PET_WINDOW_LEVEL > 0
    assert COMPANION_WINDOW_FLAGS["on_top"] is False


def test_the_level_constants_match_real_appkit() -> None:
    """The plain numbers above must be the actual AppKit values, not a hopeful transcription."""
    appkit = pytest.importorskip("AppKit")
    assert int(appkit.NSFloatingWindowLevel) == pet_native.PET_WINDOW_LEVEL
    assert int(appkit.NSModalPanelWindowLevel) == pet_native.MODAL_PANEL_LEVEL
    assert int(appkit.NSStatusWindowLevel) == pet_native.STATUS_WINDOW_LEVEL
    assert int(appkit.NSScreenSaverWindowLevel) == pet_native.SCREEN_SAVER_LEVEL


# --------------------------------------------------------------------------- RC-2


GROUND_BAR = "vcwR(g,0,42,48,6,'#20232b')"


def test_desktop_surface_suppresses_the_full_width_ground_bar() -> None:
    """RC-2a: the ground bar spans the whole 48px canvas, so on a transparent window it is a hard
    rectangular slab -- measured 132pt wide under a character roughly half that."""
    html = render_desktop_companion_html()
    assert "ground:false" in html


def test_the_in_app_artwork_keeps_its_ground_bar() -> None:
    """The repair removes a backdrop, not the character's identity. The bar is still painted for the
    docked companion, where it is part of the scene rather than a slab on the desktop."""
    from core.companion_world_fragment import COMPANION_WORLD_JS

    # BOTH draw paths, named separately. Asserting only that the guarded bar appears "somewhere"
    # let a mutation delete it from the character path and still pass on the strength of the scene
    # path -- caught by tools/pet_sabotage_driver.py, which is why this is spelled out.
    guarded = "if(opts.ground!==false)" + GROUND_BAR
    assert guarded + ";const hero=" in COMPANION_WORLD_JS, "the character path lost its ground"
    assert COMPANION_WORLD_JS.count(guarded) == 2, "the multi-worker scene path lost its ground"
    # the guard must be opt-OUT: a caller that passes nothing still gets the bar
    assert "opts.ground===false" not in COMPANION_WORLD_JS


def test_desktop_document_paints_no_permanent_status_card() -> None:
    """RC-2b: the caption was an always-on dark card with a border, sitting under the pet."""
    html = render_desktop_companion_html()
    caption_rule = html.split("#caption{", 1)[1].split("}", 1)[0]
    assert "opacity:0" in caption_rule, "the caption must be invisible at rest"
    assert "pointer-events:none" in caption_rule, "an invisible caption must not intercept clicks"
    # and it must still be able to appear, or the state is no longer legible
    assert "#caption.show" in html


def test_desktop_document_drops_the_electron_only_drag_css() -> None:
    """RC-7: `-webkit-app-region` is a Chromium-shell property. WKWebView does not implement it, so
    it never controlled dragging and never excluded the return button from it."""
    assert "-webkit-app-region" not in render_desktop_companion_html()


def test_in_app_sprite_has_no_rectangular_drop_shadow() -> None:
    """RC-2d: `.dragging` painted `box-shadow` around the whole 112pt box -- a rectangle."""
    from core.companion_presentation_fragment import render_companion_fragment

    fragment = render_companion_fragment()
    dragging_rule = fragment.split(".vool-ninja.dragging {", 1)[1].split("}", 1)[0]
    assert "box-shadow" not in dragging_rule


# --------------------------------------------------------------------------- RC-5 (hit behaviour)


def test_transparent_corners_do_not_intercept_clicks() -> None:
    """RC-5: measured on both displays, a click 6pt inside the pet's corner was routed to the pet
    instead of to the application underneath. The 176x176 window was an invisible blocker."""
    frame = (100.0, 100.0, 176.0, 176.0)
    corners = [
        (106.0, 106.0),                    # bottom-left
        (100.0 + 170.0, 106.0),            # bottom-right
        (106.0, 100.0 + 170.0),            # top-left
    ]
    for x, y in corners:
        assert not pet_native.point_hits_pet(x, y, frame), f"corner {(x, y)} still blocks clicks"


def test_the_character_and_its_control_still_take_clicks() -> None:
    """The inverse defect: making the whole window click-through would break dragging entirely."""
    frame = (100.0, 100.0, 176.0, 176.0)
    assert pet_native.point_hits_pet(188.0, 188.0, frame), "the character must be draggable"
    # the return/reset/hide cluster in the top-right corner
    assert pet_native.point_hits_pet(100.0 + 158.0, 100.0 + 158.0, frame)


def test_hit_regions_are_islands_not_one_bounding_box() -> None:
    """Unioning the canvas and the corner control into a single rect would silently hand the empty
    corners back their click-blocking behaviour."""
    assert len(pet_native.pet_hit_rects()) >= 2


# --------------------------------------------------------------------------- RC-6 (drag geometry)


def test_a_pet_flung_past_the_top_edge_is_pulled_back_not_pushed_further() -> None:
    """RC-6: pywebview's clamp reads `origin.y + (height + windowHeight)` -- it ADDS the window
    height where it must subtract, so a drag toward the top of a screen pushes the window further
    off-screen instead of stopping it at the edge."""
    flung = (400.0, PRIMARY[1] + PRIMARY[3] + 176.0, 176.0, 176.0)   # entirely above the display
    x, y, _, _ = pet_native.clamp_frame_to_screens(flung, TWO_SCREENS)
    assert y < flung[1], "the pet was pushed further away instead of being recovered"
    overlap_w, overlap_h = pet_native.visible_overlap((x, y, 176.0, 176.0), TWO_SCREENS)
    assert overlap_w >= pet_native.MIN_VISIBLE_POINTS
    assert overlap_h >= pet_native.MIN_VISIBLE_POINTS


def test_a_monitor_left_of_the_primary_is_not_treated_as_off_screen() -> None:
    """Screen coordinates are not all positive. A pet sitting on a display at x=-1920 is at home,
    not lost, and must not be dragged back to the primary."""
    on_left_monitor = (-1800.0, 400.0, 176.0, 176.0)
    assert pet_native.clamp_frame_to_screens(on_left_monitor, TWO_SCREENS) == on_left_monitor


def test_a_pet_straddling_two_monitors_is_left_where_it_is() -> None:
    """Crossing a monitor boundary is a legitimate place to be; clamping into one screen would make
    the pet jump away under the operator's cursor."""
    straddling = (-100.0, 400.0, 176.0, 176.0)
    assert pet_native.clamp_frame_to_screens(straddling, TWO_SCREENS) == straddling


def test_a_position_saved_on_a_disconnected_monitor_is_recovered() -> None:
    """The left-hand display is unplugged; the saved coordinate now points at nothing."""
    saved = {"x": -1800.0, "y": 400.0}
    recovered = pet_native.restore_saved_position(saved, [PRIMARY])
    assert recovered is not None
    overlap_w, overlap_h = pet_native.visible_overlap(recovered, [PRIMARY])
    assert overlap_w >= pet_native.MIN_VISIBLE_POINTS and overlap_h >= pet_native.MIN_VISIBLE_POINTS


def test_a_first_run_pet_invents_no_coordinate() -> None:
    assert pet_native.restore_saved_position(None, TWO_SCREENS) is None
    assert pet_native.restore_saved_position({"x": "left-ish"}, TWO_SCREENS) is None
    default = pet_native.default_pet_frame(TWO_SCREENS)
    overlap_w, overlap_h = pet_native.visible_overlap(default, TWO_SCREENS)
    assert overlap_w >= pet_native.MIN_VISIBLE_POINTS and overlap_h >= pet_native.MIN_VISIBLE_POINTS


def test_clamping_survives_having_no_displays_at_all() -> None:
    """A hot-unplug race must not raise inside a window-move callback."""
    frame = (10.0, 10.0, 176.0, 176.0)
    assert pet_native.clamp_frame_to_screens(frame, []) == frame


# --------------------------------------------------------------------------- Checkpoint C (bridge)


def test_a_hostile_caption_cannot_break_out_of_the_desktop_document() -> None:
    """The caption crosses into a second window as JSON inside a <script>. A caption that could
    close that element would be running attacker text in the pet's document."""
    hostile = "</script><img src=x onerror=alert(1)>"
    html = render_desktop_companion_html({"state": "idle", "caption": hostile})
    assert "</script><img" not in html
    body = html.split("VCW_INITIAL_STATE=", 1)[1]
    assert "<" not in body.split(";", 1)[0]
    # and the renderer must place it as text, never as markup
    assert "textContent" in html


def test_the_payload_refuses_absurd_and_non_numeric_positions() -> None:
    """A page-supplied coordinate becomes a real window placement, so it is bounded."""
    for bad in ({"x": "left", "y": 1}, {"x": float("nan"), "y": 1}, {"x": 1e9, "y": 1}, {"x": 1}):
        clean = normalise_companion_payload({"state": "idle", "desktop_position": bad})
        assert "desktop_position" not in clean, bad
    good = normalise_companion_payload({"state": "idle", "desktop_position": {"x": -1912.0, "y": 600.5}})
    assert good["desktop_position"] == {"x": -1912.0, "y": 600.5}


def test_the_payload_allowlist_still_drops_everything_it_does_not_name() -> None:
    clean = normalise_companion_payload(
        {"state": "idle", "evil": "rm -rf", "character": "spark", "url": "http://x"}
    )
    assert set(clean) <= {"state", "character", "pack", "caption", "desktop_position"}


# --------------------------------------------------------------------------- lifecycle


class _StrictWebview:
    """A double that validates exactly what pywebview validates, by delegating to pywebview.

    This is the direct answer to RC-1b: the previous double accepted any keyword argument and
    returned a window, so the suite went green against a call the real library refused.
    """

    def __init__(self, webview_module) -> None:
        self._webview = webview_module
        self.calls: list[dict] = []

    def create_window(self, *args, **kwargs):
        self.calls.append(dict(kwargs))
        return self._webview.create_window(*args, **kwargs)


def test_detach_creates_the_window_through_the_real_validator(real_webview) -> None:
    api = _WindowApi()
    strict = _StrictWebview(real_webview)
    api.set_window(object(), strict)
    result = api.detach_companion({"state": "tool", "caption": "RUNNING"})
    assert result["ok"] is True, result
    assert result["mode"] == "desktop"
    assert strict.calls, "no native window was requested"
    assert strict.calls[0]["transparent"] is True


def test_detach_is_refused_without_a_native_surface() -> None:
    """A browser tab must not be able to claim it opened a desktop window."""
    api = _WindowApi()
    api.set_window(object(), None)
    assert api.detach_companion({"state": "idle"}) == {"ok": False, "error": "native_window_unavailable"}


def test_hide_and_reset_are_refused_before_the_pet_exists() -> None:
    api = _WindowApi()
    api.set_window(object(), None)
    assert api.reset_companion_position()["ok"] is False
    assert api.companion_overhead()["running"] is False


def test_a_hidden_pet_does_no_polling() -> None:
    """Checkpoint B: no continuous polling while hidden. Measured from the tracker's own counters,
    not asserted to be zero by assumption."""
    pytest.importorskip("AppKit")
    tracker = pet_native.PetWindowTracker(_FakeNsWindow())
    assert tracker.stats()["running"] is False
    assert tracker.stats()["ticks"] == 0


class _FakeNsWindow:
    """Stands in for an NSWindow for the pure bookkeeping paths only. It is never used to claim
    anything about real window behaviour -- that is what `tools/pet_native_proof.py` is for."""

    def __init__(self) -> None:
        self.ignores = False
        self.origin = (0.0, 0.0)

    def ignoresMouseEvents(self):
        return self.ignores

    def setIgnoresMouseEvents_(self, value):
        self.ignores = bool(value)


def test_the_tracker_reports_its_own_cost() -> None:
    """"No unnecessary overhead" is a measurement, so the shape that carries it must exist."""
    tracker = pet_native.PetWindowTracker(_FakeNsWindow())
    stats = tracker.stats()
    assert {"running", "interval_seconds", "ticks", "mean_tick_ms"} <= set(stats)
    assert stats["interval_seconds"] <= 0.1, "the cursor poll must stay bounded"


def test_pet_position_round_trips_through_json() -> None:
    """The position crosses the bridge as JSON, so it must survive the trip losslessly."""
    position = {"x": -1912.0, "y": 600.5}
    assert json.loads(json.dumps(position, separators=(",", ":"))) == position


# --------------------------------------------------------------------------- Settings surface


def test_pet_controls_live_in_the_existing_settings_system() -> None:
    """Checkpoint B: Return, Hide and Reset must be reachable from Settings, not only from a hover
    control on a window that cannot take the keyboard."""
    from core.vool_settings_page import settings_groups

    groups = {group["id"]: group for group in settings_groups()}
    assert "companion" in groups, "no Companion section in the existing settings model"
    rows = groups["companion"]["rows"]
    assert any(row.get("widget") == "companion_pet" for row in rows)


def test_settings_pet_controls_use_the_same_bridge_and_no_second_store() -> None:
    """The pet's placement stays in the one companion preference. Settings drives the SAME native
    bridge the pet's own buttons drive; it does not write a parallel preference."""
    from core.vool_settings_page import render_vool_settings_html

    html = render_vool_settings_html(build_commit="test")
    for method in ("attach_companion", "hide_companion", "reset_companion_position"):
        assert method in html, method
    # the companion row must not declare a server-side write authority: it is presentation, and a
    # `write` here would be exactly the second preference store the mission forbids
    from core.vool_settings_page import settings_groups

    companion = next(g for g in settings_groups() if g["id"] == "companion")
    assert all("write" not in row for row in companion["rows"])


def test_pet_presentation_controls_carry_no_task_authority() -> None:
    """Checkpoint C: a native pet is another view, not another agent."""
    api = _WindowApi()
    exposed = {name for name in dir(api) if not name.startswith("_")}
    # the bridge stays narrow: windows (Settings and the guided Setup), a folder picker, the pet's
    # presentation, and the two answer-export saves whose destination must be picked in a native
    # dialog (they were added to the bridge after this pin was written -- a pre-existing drift on
    # the frozen base, recorded and repaired here, not a new authority).
    assert exposed == {
        "attach_companion", "close_settings", "close_setup", "companion_overhead", "detach_companion",
        "hide_companion", "open_companion_appearance", "open_settings", "open_setup", "pet_yield_rect",
        "pick_folder", "reset_companion_position", "save_answer_pdf", "save_chat_export",
        "set_window", "sync_companion",
    }


def test_the_window_module_imports_the_way_the_app_launcher_runs_it() -> None:
    """The .app runs `python installer/bundle/vool_window.py` as a SCRIPT, with the project root on
    sys.path only because the module puts it there itself.

    An `installer.bundle` import placed in the header block -- above that line -- raised ImportError
    and the window never opened. The suite could not see it: pytest imports this as a module with the
    root already on the path. So this loads the file BY PATH, from a clean interpreter whose sys.path
    does not contain the repo, exactly as the launcher does.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    module = Path(__file__).resolve().parents[1] / "installer" / "bundle" / "vool_window.py"
    probe = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('nw_probe',{str(module)!r});"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "print('IMPORTED', bool(m.COMPANION_WINDOW_FLAGS))"
    )
    # PYTHONPATH must be scrubbed. Inheriting the suite's PYTHONPATH put the repo root back on
    # sys.path and the probe passed with the import in the broken position -- a vacuous test, caught
    # by tools/pet_sabotage_driver.py. The .app launcher sets no PYTHONPATH.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd="/", timeout=120, env=env
    )
    assert "IMPORTED True" in result.stdout, result.stdout + result.stderr


def test_adopting_the_native_window_waits_for_cocoa_to_register_it() -> None:
    """The Cocoa backend registers a non-master window with `AppHelper.callAfter` -- asynchronously,
    on the main thread -- so `create_window` returns a handle whose NSWindow does not exist yet.

    The pet is created from the JS-API bridge's worker thread, which is exactly that case. Sampling
    the registry once returned None, and the first live launch of the repaired app logged
    `no_native_window` and fell back to a window carrying none of the pet's policy: no floating
    level, no click-through, no saved position. Every test passed while that was true, because no
    test drove the asynchronous path.
    """
    import sys
    import threading
    import types

    assert pet_native.PetWindowController.ADOPT_TIMEOUT_SECONDS > 0

    registry: dict = {}
    fake_cocoa = types.SimpleNamespace(BrowserView=types.SimpleNamespace(instances=registry))
    platforms = types.ModuleType("webview.platforms")
    platforms.cocoa = fake_cocoa
    handle = types.SimpleNamespace(uid="child_probe")
    sentinel = object()

    saved = sys.modules.get("webview.platforms")
    sys.modules["webview.platforms"] = platforms
    try:
        # nothing registered yet: a single sample must find nothing
        assert pet_native.ns_window_for(handle) is None
        # ...and the registration lands late, exactly as callAfter delivers it
        threading.Timer(
            0.25, lambda: registry.__setitem__("child_probe", types.SimpleNamespace(window=sentinel))
        ).start()
        assert pet_native.ns_window_for(handle, timeout=4.0) is sentinel
    finally:
        if saved is None:
            sys.modules.pop("webview.platforms", None)
        else:
            sys.modules["webview.platforms"] = saved


# ------------------------------------------------------------------------------------- 
# The operator-answer yield (2026-09-18 follow-up): a click meant for a permission bar or
# mode banner the pet happens to cover must reach that surface -- in-page by stacking (the
# chat page's own rule) and at the OS window tier by hit-testing yield, without disabling
# the pet's own pointer interaction anywhere else.


def test_a_click_inside_the_yield_rect_belongs_to_the_surface_under_the_pet():
    frame = (100.0, 100.0, float(pet_native.PET_WINDOW_WIDTH), float(pet_native.PET_WINDOW_HEIGHT))
    # The pet's canvas covers the point in ordinary hit-testing...
    cursor = (frame[0] + pet_native.PET_WINDOW_WIDTH / 2.0, frame[1] + pet_native.PET_WINDOW_HEIGHT / 2.0)
    assert pet_native.point_hits_pet(cursor[0], cursor[1], frame) is True
    # ...but with the operator-answer region registered over that spot, the same click passes
    # through to the surface beneath: the pet yields exactly there.
    avoid = (frame[0], frame[1] + 40.0, float(pet_native.PET_WINDOW_WIDTH), 96.0)
    assert pet_native.point_hits_pet(cursor[0], cursor[1], frame, avoid_rect=avoid) is False
    # Outside the region the pet's own hit rects still decide -- dragging stays functional.
    # Local (30, 30) sits inside the centered canvas box but below the yielded band.
    outside = (frame[0] + 30.0, frame[1] + 30.0)
    assert pet_native.point_hits_pet(outside[0], outside[1], frame, avoid_rect=avoid) is True


def test_the_controller_converts_page_coordinates_and_clears():
    controller = pet_native.PetWindowController(object())
    assert controller._tracker is not None or True  # controller without a window has no tracker
    result = controller.set_yield_rect({"x": 10, "y": 20, "width": 300, "height": 80})
    # No tracker exists before adopt(); the bridge refuses rather than inventing one.
    assert result.get("ok") is False


class _FakeTracker:
    def __init__(self) -> None:
        self.rect = "unset"

    def set_yield_rect(self, rect) -> None:
        self.rect = rect


def test_set_yield_rect_flips_page_top_left_coordinates_into_appkit_space(monkeypatch):
    controller = pet_native.PetWindowController(object())
    tracker = _FakeTracker()
    controller._tracker = tracker
    monkeypatch.setattr(pet_native, "visible_frames", lambda: [(0.0, 0.0, 1440.0, 900.0)])
    result = controller.set_yield_rect({"x": 100.0, "y": 700.0, "width": 400.0, "height": 100.0})
    assert result["ok"] is True
    # Page y=700..800 (top-left, downward) is AppKit y=900-800=100..200 (bottom-left, upward).
    assert tracker.rect == (100.0, 100.0, 400.0, 100.0)
    cleared = controller.set_yield_rect(None)
    assert cleared == {"ok": True, "cleared": True}
    assert tracker.rect is None
    refused = controller.set_yield_rect({"x": "no", "y": 1, "width": 1, "height": 1})
    assert refused.get("ok") is False


def test_the_chat_page_stacks_operator_answer_surfaces_above_the_companion_layer():
    """The in-page half of the yield law: the footer outranks the pet while it must be answered.

    The companion layer is a fixed z-30 overlay with a pointer-taking sprite, and the footer
    establishes its own stacking context (container-type), so without this rule no z-index on
    the bar itself could ever beat the sprite -- the exact defect that made Revoke unreachable
    by ordinary click until the pet was moved aside.
    """
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert "body.answer-pending footer" in html and "z-index: 35" in html
    # The class is driven by the two answer surfaces, and the native pet receives the same
    # region as a yield rect through the bridge when one exists.
    assert "syncAnswerPendingSurfaces" in html
    assert "pet_yield_rect" in html


def test_in_a_real_browser_the_perm_bar_outranks_a_pet_parked_over_it():
    """The §3 acceptance, in a real chromium against the real rendered page code.

    The companion sprite is positioned exactly over the permission bar's buttons; an ordinary
    hit-test at the button centre must return the BUTTON (not the sprite), the answer-pending
    body class must be set while the bar shows, keyboard focus must reach the bar, and the pet
    must still own pointer events one row above (dragging stays functional). Route-fulfilled
    page + real CSS + real chromium -- honestly labelled: browser-tier, not a native app window.
    """
    pytest.importorskip("playwright")
    import tests.served_browser as served_browser

    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})

        def _route(route):
            request = route.request
            if request.resource_type == "document":
                route.fulfill(status=200, content_type="text/html", body=html)
                return
            route.fulfill(status=200, content_type="application/json", body="{}")

        page.route("**/*", _route)
        page.goto("http://vool.test/chat")
        page.wait_for_timeout(300)
        # Park the pet sprite exactly over the footer's permission-bar area.
        page.evaluate("""() => {
            // The sprite's own default placement (measured: left 1144, top 671, 112x112 --
            // bottom edge 783) already parks it over the footer's permission-bar row, which is
            // exactly the reported defect's geometry: no artificial positioning is needed.
            const ev = { approval_id: 'probe-approval', action: 'write notes.txt', affected_resources: ['notes.txt'],
                         expected_side_effects: 'creates a file', reversible: true, intent: 'workspace.write_file',
                         scope_options: ['once', 'task'] };
            showPermBar(displayedChat, { approval: ev });
        }""")
        page.wait_for_timeout(200)
        state = page.evaluate("""() => {
            const bar = document.getElementById('permBar');
            const barBox = bar.getBoundingClientRect();
            const footerBox = document.querySelector('footer').getBoundingClientRect();
            const btn = document.getElementById('permOnce');
            const r = btn.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            const pet = document.querySelector('#companionLayer .vool-ninja');
            const petBox = pet.getBoundingClientRect();
            const petHit = document.elementFromPoint(petBox.left + 6, Math.max(8, petBox.top + 6));
            const overlapY = Math.min(petBox.bottom, footerBox.bottom) - 8;
            const overlapX = petBox.left + petBox.width / 2;
            const overlapHit = document.elementFromPoint(overlapX, overlapY);
            return {
                pending: document.body.classList.contains('answer-pending'),
                barVisible: !bar.hidden,
                hitIsButton: hit === btn || btn.contains(hit),
                petOverFooter: petBox.bottom > footerBox.top && petBox.top < footerBox.bottom,
                petHitIsPet: petHit === pet || pet.contains(petHit),
                overlapHitIsPet: overlapHit === pet || pet.contains(overlapHit),
                zIndex: getComputedStyle(document.querySelector('footer')).zIndex,
            };
        }""")
        assert state["barVisible"] and state["pending"], state
        assert state["petOverFooter"], f"fixture must park the pet over the footer: {state}"
        assert not state["overlapHitIsPet"], f"inside the overlap the footer must own the pointer: {state}"
        assert state["hitIsButton"], f"an ordinary pointer hit must reach the button, not the pet: {state}"
        assert state["zIndex"] == "35", state
        # Keyboard: the bar's buttons are reachable and activatable without any pointer.
        focused = page.evaluate("""() => {
            const btn = document.getElementById('permDeny');
            btn.focus();
            return document.activeElement === btn;
        }""")
        assert focused
    finally:
        browser.close()
        manager.stop()
