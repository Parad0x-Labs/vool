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
