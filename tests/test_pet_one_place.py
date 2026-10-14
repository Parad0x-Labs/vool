"""One pet, in one place, that the user can always see while customising it.

Live report on the v0.7.0-beta build (2026-10-13):
* Settings > Appearance offered "Move to desktop". Pressing it called the native bridge from the
  Settings window, so the chat page never learned the pet had left: one pet stayed in the app and
  a second one appeared on the desktop. The pet leaves the window by being dragged out of it.
* "Customise pet" opened a panel over the right side of the window, which is exactly where the
  docked pet sits, so the user could not see the pet they were changing.
"""

from __future__ import annotations

import json
import re

from core.companion_drawer_fragment import render_companion_drawer_fragment
from core.vool_settings_page import render_vool_settings_html
from installer.bundle import vool_window
from installer.bundle.vool_window import _WindowApi
from tests.chat_page_js_harness import DOM, run_node
from tests.test_native_companion_window import _FakeWebview, _FakeWindow


def test_settings_offers_rescue_controls_but_no_second_way_to_spawn_a_pet() -> None:
    html = render_vool_settings_html(build_commit="test")
    actions = re.search(r"const actions = \[(.*?)\];", html, re.DOTALL)
    assert actions, "the desktop pet rescue controls must still render"
    body = actions.group(1)
    assert "detach_companion" not in body and "Move to desktop" not in body
    for method in ("attach_companion", "hide_companion", "reset_companion_position"):
        assert method in body, method


class _BrokenController:
    """A native controller whose adopt fails after pywebview already created the window."""

    def __init__(self, _window) -> None:
        self.on_position_settled = None

    @property
    def available(self) -> bool:
        return False

    def adopt(self, _position=None):
        raise RuntimeError("cocoa refused the window")


def test_a_failed_detach_does_not_leave_a_second_pet_on_the_desktop(monkeypatch) -> None:
    monkeypatch.setattr(vool_window.pet_native, "PetWindowController", _BrokenController)
    main = _FakeWindow()
    webview = _FakeWebview()
    api = _WindowApi()
    api.set_window(main, webview)

    result = api.detach_companion({"state": "idle"})

    # The page keeps (or returns to) its in-app pet on a failed detach, so the window that was
    # created before the failure must not stay up beside it.
    assert result == {"ok": False, "error": "RuntimeError"}
    assert webview.window.hidden == 1


DRIVER = r"""
const res = { errors: [], draws: [] }, ctxs = [];
try {
  localStorage.setItem('vool_ninja_pos_v1', JSON.stringify({ personality: 'rascal', pack: 'ninja' }));
  window.VoolCompanionWorld = {
    characters: { spark: { name: 'Spark' }, rascal: { name: 'Rascal' } },
    packs: { default: { label: 'default' }, ninja: { label: 'ninja' } },
    frameAt: () => 0,
    draw: (ctx, state, frame, opts) => { ctxs.push(ctx); res.draws.push({ state, character: opts.character, pack: opts.pack }); },
  };
  let scheduled = 0, cancelled = 0;
  globalThis.requestAnimationFrame = () => { scheduled++; return scheduled; };
  globalThis.cancelAnimationFrame = () => { cancelled++; };
  window.VoolCompanion = { chooseCharacter(id) {
    localStorage.setItem('vool_ninja_pos_v1', JSON.stringify({ personality: id, pack: 'ninja' }));
  } };
  __DRAWER__
  const drawer = window.VoolCompanionDrawer;
  drawer.open();
  const root = document.body.children.find((c) => c.id === 'vcdDrawer');
  res.htmlHasPreview = root.querySelector('.vcd-body').innerHTML.includes('id="vcdPreview"');
  res.drawnIntoPreview = ctxs.length > 0 && ctxs[0] === root.querySelector('#vcdPreview').getContext('2d');
  res.afterOpen = res.draws.slice();
  window.VoolCompanion.chooseCharacter('spark');
  drawer.close(); drawer.open();
  res.afterChange = res.draws[res.draws.length - 1];
  drawer.close();
  res.cancelled = cancelled;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_customising_the_pet_shows_a_live_preview_inside_the_panel() -> None:
    drawer_js = re.search(r"<script>(.*?)</script>", render_companion_drawer_fragment(), re.DOTALL).group(1)
    program = DOM + "\nwindow.localStorage = localStorage;\n" + DRIVER.replace("__DRAWER__", drawer_js)
    out = run_node(program)
    assert not out["errors"], out["errors"]
    assert out["htmlHasPreview"], "the panel must carry its own preview of the pet"
    assert out["afterOpen"], "opening the panel must draw the pet into its preview"
    first = out["afterOpen"][0]
    assert first == {"state": "idle", "character": "rascal", "pack": "ninja"}, json.dumps(first)
    assert out["drawnIntoPreview"], "the pet is drawn into the panel's own canvas"
    assert out["afterChange"]["character"] == "spark", "the preview follows the chosen character"
    assert out["cancelled"] >= 1, "closing the panel stops the preview animation"


class _Controller:
    """A native controller whose pet window is not on the desktop (hidden or returned)."""

    def __init__(self, _window=None, *, on_desktop: bool = False) -> None:
        self.on_position_settled = None
        self.on_desktop = on_desktop
        self.adopted: list[object] = []

    @property
    def available(self) -> bool:
        return True

    def adopt(self, position=None):
        self.adopted.append(position)
        return {"ok": True}

    def position(self):
        return {"x": 640.0, "y": 300.0}

    def reset_position(self):
        return {"x": 1.0, "y": 2.0} if self.on_desktop else None


def test_reset_does_not_bring_back_a_pet_that_is_not_on_the_desktop() -> None:
    main = _FakeWindow()
    api = _WindowApi()
    api.set_window(main, _FakeWebview())
    api._pet = _Controller(on_desktop=False)

    assert api.reset_companion_position() == {"ok": False, "error": "not_on_desktop"}
    assert main.scripts == [], "a refused reset must not report a desktop position to the page"

    api._pet = _Controller(on_desktop=True)
    assert api.reset_companion_position()["ok"] is True


def test_dragging_the_pet_out_remembers_where_it_was_dropped(monkeypatch) -> None:
    monkeypatch.setattr(vool_window.pet_native, "PetWindowController", _Controller)
    monkeypatch.setattr(vool_window.pet_native, "cursor_position", lambda: {"x": 640.0, "y": 300.0})
    main = _FakeWindow()
    api = _WindowApi()
    api.set_window(main, _FakeWebview())

    assert api.detach_companion({"state": "idle", "at_pointer": True})["ok"] is True
    assert api._pet.adopted == [{"x": 640.0, "y": 300.0}], "the pet appears where the pointer let go"
    assert any("desktopMoved" in script and '"x":640.0' in script for script in main.scripts), (
        "the drop spot must reach the page's one preference so a restart puts the pet back there"
    )


def _boot_with(stored: dict) -> dict:
    from tests.test_companion_one_drag_finality import page_scripts

    program = (
        DOM
        + "\nlocalStorage.setItem('vool_ninja_pos_v1', " + json.dumps(json.dumps(stored)) + ");\n"
        + "globalThis.__detaches = 0;\n"
        + "window.pywebview = { api: { detach_companion: () => { __detaches++; return Promise.resolve({ ok: true }); },"
        + " sync_companion: () => Promise.resolve({ ok: true }) } };\n"
        + "\n;(function(){\n" + "\n;\n".join(page_scripts()) + "\n})();\n"
        + "await new Promise((r) => _st(r, 600));\n"
        + "out({ detaches: __detaches, pos: window.VoolCompanion.pos() });\n"
    )
    return run_node(program, timeout=120)


def test_a_pet_hidden_from_the_desktop_stays_hidden_after_a_restart() -> None:
    hidden = _boot_with({"mode": "desktop", "hidden": True, "personality": "spark"})
    assert hidden["detaches"] == 0, "a hidden desktop pet must not reappear on launch"
    shown = _boot_with({"mode": "desktop", "hidden": False, "personality": "spark"})
    assert shown["detaches"] >= 1, "a visible desktop pet is still restored on launch"


FLASH_DRIVER = r"""
const res = { errors: [] };
try {
  const VC = window.VoolCompanion;
  window.pywebview = { api: { detach_companion: () => new Promise(function () {}),
                              sync_companion: () => Promise.resolve({ ok: true }) } };
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");
  const startX = parseFloat(sprite.style.left) || 0, startY = parseFloat(sprite.style.top) || 0;
  sprite.__on.pointerdown({ pointerId: 1, clientX: startX + 56, clientY: startY + 56 });
  __winH.pointermove[__winH.pointermove.length - 1]({ pointerId: 1, clientX: 1199, clientY: 300 });
  __winH.pointerup[__winH.pointerup.length - 1]({ pointerId: 1, clientX: 1199, clientY: 300 });
  res.atDrop = sprite.style.display;
  // The desktop window is still being created: the paint loop and the 1.2s tick keep running.
  for (let i = 0; i < 3; i++) VC.paintNow(Date.now() + i * 1300);
  res.whilePending = sprite.style.display;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_the_in_app_pet_stays_gone_while_its_desktop_window_is_created() -> None:
    from tests.test_companion_one_drag_finality import run_driver

    out = run_driver(FLASH_DRIVER)
    assert not out["errors"], out["errors"]
    assert out["atDrop"] == "none", "the pet leaves the window at the drop"
    assert out["whilePending"] == "none", "repaints must not bring a second pet back meanwhile"
