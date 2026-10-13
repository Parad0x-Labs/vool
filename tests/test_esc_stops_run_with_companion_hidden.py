"""Esc must stop a live run even though the companion shells sit in the page hidden.

The chat footer promises "Esc stop". The palette fragment's Esc cascade snapshots "was a
legacy surface open?" in the capture phase, and the companion fragment creates its
menu/pop/lab nodes at boot, hiding them with display:none until opened. A presence-only
check therefore reads "a menu is open" on every page load, and the run stopper never fires.

These tests execute the REAL cascade, extracted verbatim from the served page, under a
minimal DOM whose three shells mirror the real boot state, and dispatch a real Escape keydown
through the listeners the cascade registers. No browser is needed for the ownership question;
the last test drives the real served page in Chromium.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html

_STUB = """
function El(display, computed) {
  this.style = { display: display };
  this.hidden = false;
  this._computed = computed === undefined ? display : computed;
}
// The three companion shells exactly as the companion fragment boots them: present in the
// page, hidden with inline display:none. `open` rewrites one shell per scenario.
const shells = {
  menu: new El('none'), pop: new El('none'), lab: new El('none'),
};
const legacyOverlays = {};   // id -> El, only populated by the overlay scenario
const listeners = { capture: [], bubble: [] };
globalThis.document = {
  getElementById: (id) => legacyOverlays[id] || null,
  querySelector: (sel) => {
    if (sel.indexOf('.vool-ninja-menu') >= 0) return shells.menu;
    if (sel.indexOf('.vool-ninja-pop') >= 0) return shells.pop;
    if (sel.indexOf('.vool-character-lab') >= 0) return shells.lab;
    return null;
  },
  querySelectorAll: (sel) => {
    const out = [];
    if (sel.indexOf('.vool-ninja-menu') >= 0) out.push(shells.menu);
    if (sel.indexOf('.vool-ninja-pop') >= 0) out.push(shells.pop);
    if (sel.indexOf('.vool-character-lab') >= 0) out.push(shells.lab);
    return out;
  },
  addEventListener: (type, fn, capture) => {
    if (type === 'keydown') listeners[capture ? 'capture' : 'bubble'].push(fn);
  },
};
// A shell the stylesheet hides by class (.vool-character-lab defaults to display:none) carries
// no inline style; the cascade must read the cascade, not just el.style, to see that. Inline
// style wins over the class rule, exactly as in a browser.
globalThis.window = {
  getComputedStyle: (el) => ({
    display: el.style.display !== '' ? el.style.display : el._computed,
    visibility: 'visible',
  }),
};
let stopCalls = 0;
const page = {
  hasLiveRun: () => true,
  stopDisplayedRun: () => { stopCalls += 1; return true; },
  toast: () => {},
};
globalThis.pageActions = () => page;
globalThis.isEditable = () => false;
globalThis.overlay = { hidden: true };   // the palette's own overlay, declared earlier in the fragment
"""


def _cascade_source() -> str:
    """The Esc cascade, verbatim out of the served page."""
    html = render_vool_chat_html()
    start = html.index("var escSawOpenSurface = false;")
    end = html.index("window.VoolPalette = Object.freeze")
    return html[start:end]


def _press_esc(scenario: str) -> int:
    """Run the real cascade under node for one scenario; return stopDisplayedRun calls."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat page script")
    setup = {
        # boot state: every shell hidden, nothing else open
        "hidden": "",
        # the companion code opens the ninja menu with inline display:block
        "menu-open": "shells.menu.style.display = 'block';",
        "pop-open": "shells.pop.style.display = 'block';",
        "lab-open": "shells.lab.style.display = 'flex';",
        # a class-level hide (no inline style): visible only through getComputedStyle
        "css-hidden-lab": (
            "shells.lab.style.display = ''; shells.lab._computed = 'none';"
        ),
        # a legacy settings overlay is genuinely open (hidden attribute cleared)
        "settings-overlay-open": (
            "legacyOverlays['settingsOverlay'] = new El('block');"
            "legacyOverlays['settingsOverlay'].hidden = false;"
        ),
    }[scenario]
    program = (
        _STUB
        + _cascade_source()
        + "\n"
        + setup
        + "\n"
        + """
const ev = { key: 'Escape', defaultPrevented: false, target: { id: 'input' } };
for (const fn of listeners.capture) fn(ev);
for (const fn of listeners.bubble) fn(ev);
console.log(JSON.stringify({ stopCalls }));
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"the cascade failed under node:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])["stopCalls"]


def test_esc_stops_the_run_when_the_companion_shells_are_hidden() -> None:
    """Bare Esc with a live run and nothing visible: the footer's Esc stop must fire."""
    assert _press_esc("hidden") == 1


@pytest.mark.parametrize(
    "scenario", ["menu-open", "pop-open", "lab-open", "settings-overlay-open"]
)
def test_esc_defers_to_a_surface_the_user_can_see(scenario: str) -> None:
    """An open companion menu/pop/lab (or legacy overlay) owns Esc; the run keeps going."""
    assert _press_esc(scenario) == 0


def test_class_hidden_shell_does_not_count_as_open() -> None:
    """A shell hidden by the stylesheet (no inline style) is closed as far as Esc cares."""
    assert _press_esc("css-hidden-lab") == 1


_STOP_SPY = """() => {
  const real = window.VoolPageActions;
  window.__escStops = 0;
  // A live run is simulated at the page-actions seam only; the palette's real Esc cascade,
  // the real companion shells and the real stylesheet all run unchanged.
  window.VoolPageActions = Object.assign({}, real, {
    hasLiveRun: () => true,
    stopDisplayedRun: () => { window.__escStops += 1; return true; },
    toast: () => {},
  });
}"""


def test_served_chat_page_esc_stops_the_run_and_defers_to_the_open_lab(tmp_path) -> None:
    """The real /chat page in Chromium: with the companion booted (its shells present and
    hidden) a bare Esc from the composer reaches the run stopper; with the character lab
    open, Esc belongs to the lab and the run is not stopped."""
    import tests._reader_served_rig as rig
    from tests.served_browser import launch_chromium

    with rig.CapturingProvider() as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        daemon.start(timeout=120)
        manager, browser = launch_chromium()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
            page.wait_for_selector("#input")
            page.wait_for_selector(".vool-ninja-menu", state="attached")
            page.wait_for_selector(".vool-character-lab", state="attached")
            page.evaluate(_STOP_SPY)
            page.locator("#input").focus()
            page.keyboard.press("Escape")
            assert page.evaluate("window.__escStops") == 1

            page.evaluate("VoolCompanion.openCharacterLab()")
            page.wait_for_selector(".vool-character-lab", state="visible")
            page.keyboard.press("Escape")
            assert page.evaluate("window.__escStops") == 1
            assert not errors, errors
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
