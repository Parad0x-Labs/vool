"""One-drag finality and the stray corner dot -- the desktop-usability pet placement laws.

NEW regressions for product/desktop-usability-20260917, distinct from the existing companion
suites: those pin the reducer/caption/dwell truth; these drive the REAL pointer pipeline
(pointerdown → pointermove → pointerup through the page's own captured listeners) and assert
PLACEMENT finality:

* the FIRST drop lands where it was released (the old contract slid the first drop off the
  composer's critical controls and honoured only a second attempt at the same spot -- the
  live-reported "one drag often fails to hold; a second drag is required");
* the settled position survives the periodic re-assert paint (polling) and a reload of the
  stored preference;
* a resting position that covers a composer control is visibly flagged, never silently moved;
* the sprite mounts no corner status dot (the stray ~1cm-right-of-the-feet mark), while the
  restore control keeps its own dot.

The native half (a second drag owner) is covered separately in
``tests/test_desktop_pet_drag_settling.py``.
"""

from __future__ import annotations

import re

from core.companion_presentation_fragment import render_companion_fragment
from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

FRAGMENT = render_companion_fragment()
HTML = render_vool_chat_html()


def page_scripts() -> list[str]:
    found = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    assert len(found) >= 2, "expected house + companion scripts"
    return found


def run_driver(driver: str) -> dict:
    """Boot the real page + fragment under node once and run one driver program."""
    program = (
        DOM
        + "\nglobalThis.__winH = {};\n"
        + "window.addEventListener = (t, f) => { (__winH[t] = __winH[t] || []).push(f); };\n"
        + "\n;(function(){\n"
        + "\n;\n".join(page_scripts())
        + "\n"
        + driver
        + "\n})();\n"
    )
    result = run_node(program, timeout=120)
    assert not result.get("errors"), f"page booted with errors: {result['errors']}"
    return result


# A drag onto the composer's #send control (stub rect 0,0,100,100), through the page's own
# pointer pipeline. This is the exact gesture that used to require a second drag.
DRIVER_DROP_ON_SEND = r"""
const res = { errors: [] };
try {
  const VC = window.VoolCompanion;
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");  // the harness-established sprite lookup (className is not classList-synced in the stub)
  const handlers = __winH;
  const down = sprite.__on && sprite.__on.pointerdown;
  if (!down) throw new Error("sprite has no pointerdown handler");
  // Grab the sprite at its centre (+56), drag it onto #send (rect 0,0,100,100), and release
  // so the sprite's TOP-LEFT lands exactly at (40,40) -- inside the covered rect.
  const startX = parseFloat(sprite.style.left) || 0, startY = parseFloat(sprite.style.top) || 0;
  const grabDx = 56, grabDy = 56, wantX = 40, wantY = 40;
  const relX = startX + grabDx + (wantX - startX), relY = startY + grabDy + (wantY - startY);
  down({ pointerId: 1, clientX: startX + grabDx, clientY: startY + grabDy });
  const move = handlers.pointermove[handlers.pointermove.length - 1];
  move({ pointerId: 1, clientX: relX, clientY: relY });
  const up = handlers.pointerup[handlers.pointerup.length - 1];
  up({ pointerId: 1, clientX: relX, clientY: relY });
  res.droppedAt = [VC.pos().x, VC.pos().y];
  res.mode = VC.pos().mode;
  res.stored = JSON.parse(localStorage.getItem("vool_ninja_pos_v1") || "null");
  // Polling: the 1.2s re-assert tick repainting truth must not move the settled pet.
  for (let i = 0; i < 5; i++) VC.paintNow(Date.now() + i * 1300);
  res.afterPolling = [VC.pos().x, VC.pos().y];
  res.spriteLeft = sprite.style.left; res.spriteTop = sprite.style.top;
  // The covering state is VISIBLE (dimmed), not silently moved away.
  res.coveringFlag = sprite.classList.contains("over-critical");
  // A SECOND gesture to a different uncovered point also lands first-try.
  const startX2 = VC.pos().x, startY2 = VC.pos().y;
  const rel2X = startX2 + 56 + (500 - startX2), rel2Y = startY2 + 56 + (400 - startY2);
  down({ pointerId: 2, clientX: startX2 + 56, clientY: startY2 + 56 });
  move({ pointerId: 2, clientX: rel2X, clientY: rel2Y });
  up({ pointerId: 2, clientX: rel2X, clientY: rel2Y });
  res.secondDrop = [VC.pos().x, VC.pos().y];
  res.secondStored = JSON.parse(localStorage.getItem("vool_ninja_pos_v1") || "null");
  // Reload the stored preference like a fresh boot would: the position restores exactly.
  VC.loadPos();
  res.restored = [VC.pos().x, VC.pos().y];
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_first_drop_lands_where_released_and_holds() -> None:
    out = run_driver(DRIVER_DROP_ON_SEND)
    assert not out["errors"], out["errors"]
    assert out["mode"] == "free", "a drag leaves the docked home"
    # The released point (40,40) is inside the legal window bounds, so it must be FINAL: the
    # baseline slid this drop to the side and only a second attempt held.
    assert out["droppedAt"] == [40, 40], f"first drop moved: {out['droppedAt']}"
    assert out["afterPolling"] == [40, 40], f"polling repainted the pet elsewhere: {out['afterPolling']}"
    assert out["spriteLeft"] == "40px" and out["spriteTop"] == "40px", "DOM position must match"
    assert out["stored"]["x"] == 40 and out["stored"]["y"] == 40, "the settle persists"
    # Covering #send is SHOWN, never silently moved.
    assert out["coveringFlag"] is True, "a resting drop on #send must show the covering flag"
    # A different, uncovered point: also first-try final (no critical rect at 500,400).
    assert out["secondDrop"] == [500, 400], f"second gesture moved: {out['secondDrop']}"
    assert out["secondStored"]["x"] == 500 and out["secondStored"]["y"] == 400
    assert out["restored"] == [500, 400], "a reload of the preference restores the settled spot"


def test_drag_cancel_reverts_to_last_stable_position() -> None:
    """pointercancel must not strand a half-dragged sprite (valid-behavior preservation)."""
    out = run_driver(r"""
const res = { errors: [] };
try {
  const VC = window.VoolCompanion;
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");  // the harness-established sprite lookup (className is not classList-synced in the stub)
  const startX = parseFloat(sprite.style.left) || 0, startY = parseFloat(sprite.style.top) || 0;
  res.preDrag = [startX, startY];
  res.modeBefore = VC.pos().mode;
  sprite.__on.pointerdown({ pointerId: 3, clientX: startX + 56, clientY: startY + 56 });
  const move = __winH.pointermove[__winH.pointermove.length - 1];
  move({ pointerId: 3, clientX: 300, clientY: 200 });
  const cancel = __winH.pointercancel[__winH.pointercancel.length - 1];
  cancel({ pointerId: 3 });
  res.afterCancel = [VC.pos().x, VC.pos().y];
  res.spriteAt = [sprite.style.left, sprite.style.top];
  res.draggingClass = sprite.classList.contains("dragging");
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    # Cancel reverts to the last stable position: the docked home (mode unchanged, stored
    # free point unchanged) is reapplied exactly.
    assert out["draggingClass"] is False, "the dragging visual must clear on cancel"
    assert out["spriteAt"] == [f"{out['preDrag'][0]}px", f"{out['preDrag'][1]}px"], (
        f"cancel must restore the pre-drag position {out['preDrag']}, got {out['spriteAt']}"
    )
    assert out["afterCancel"] == [0, 0] or out["modeBefore"] == "free", (
        "a cancelled docked drag must not write a stored free point"
    )


def test_no_stray_corner_dot_on_the_sprite() -> None:
    """The sprite must not mount the 9px corner tone dot; the restore control keeps its own."""
    out = run_driver(r"""
const res = { errors: [] };
try {
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");  // the harness-established sprite lookup (className is not classList-synced in the stub)
  const spriteDotChildren = sprite.children.filter((c) => c.classList && c.classList.contains("vn-dot"));
  res.spriteDots = spriteDotChildren.length;
  const restore = layer.children.find((c) => c.className === "vn-restore");
  res.restorePresent = !!restore;
  res.restoreHasDot = restore ? restore.innerHTML.indexOf("vn-dot") !== -1 : false;
  res.spriteChildClasses = sprite.children.map((c) => (c.className || ""));
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert out["spriteDots"] == 0, f"stray dot on the sprite: children={out['spriteChildClasses']}"
    # The restore control's dot is part of a real button and stays.
    assert out["restorePresent"] and out["restoreHasDot"], "the restore control keeps its dot"
    classes = " ".join(out["spriteChildClasses"])
    assert "vn-caption" in classes, "the caption (tone word carrier) remains on the sprite"


def test_keyboard_move_mode_still_sets_final_positions() -> None:
    """Accessibility preservation: arrows in move mode still place the sprite, first press."""
    out = run_driver(r"""
const res = { errors: [] };
try {
  const VC = window.VoolCompanion;
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");  // the harness-established sprite lookup (className is not classList-synced in the stub)
  const key = (k, shift) => sprite.__on.keydown({ key: k, shiftKey: !!shift, preventDefault() {} });
  key("Enter");
  key("ArrowRight"); key("ArrowDown", true);
  key("Escape");
  res.afterKeys = [VC.pos().x, VC.pos().y];
  res.mode = VC.pos().mode;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert out["mode"] == "free", "keyboard move mode places the sprite in free mode"
    # Docked home x/y start at 0,0 in the stub; right+down must move it first press.
    assert out["afterKeys"][0] > 0 or out["afterKeys"][1] > 0, out["afterKeys"]


def test_docked_home_clears_controls_exposed_by_its_first_move() -> None:
    result = run_driver(r"""
window.innerWidth = 1200; window.innerHeight = 800;
const query = document.querySelector.bind(document);
const rects = {
  "#input": {left: 1000, right: 1200, top: 610, bottom: 720, width: 200, height: 110},
  "#setupLine": {left: 1000, right: 1200, top: 480, bottom: 520, width: 200, height: 40}
};
document.querySelector = (s) => rects[s] ? {getBoundingClientRect: () => rects[s]} :
  (["#send", "#permBar", "#cloudPill", ".tc-stop", ".proj-menu", "#attachStrip"].includes(s) ? null : query(s));
window.VoolCompanion.dock();
const layer = document.body.children.find(c => c.id === "companionLayer");
const sprite = layer.children.find(c => c.attrs && c.attrs.role === "button");
out({top: parseFloat(sprite.style.top), mode: window.VoolCompanion.pos().mode});
""")
    assert result["mode"] == "docked"
    assert result["top"] + 112 < 480, result
