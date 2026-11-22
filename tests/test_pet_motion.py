"""Painted pets in motion (owner, 2026-11-22): real movement with continuity, and a move for the work.

The owner's rule: a pet never stands in one frame and lies in the next; it sits down, lies down and
gets up again. These run the real shared renderer in a real browser on one canvas per pet, with
the page clock given frame by frame, and measure how much each frame changes from the last.
"""

from __future__ import annotations

import base64

import pytest

from core.companion_painted_fragment import PAINTED_ROSTER
from core.companion_pet_rig_art import RIG_ART, RIG_META
from core.companion_world_fragment import COMPANION_WORLD_JS
from tests.served_browser import launch_chromium

ACTIVITIES = ["read", "search", "dig", "code", "exec", "test", "watch"]


@pytest.fixture(scope="module")
def page():
    manager, browser = launch_chromium()
    pg = browser.new_page(device_scale_factor=2)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.set_content("<html><body></body><script>" + COMPANION_WORLD_JS + "</script></html>")
    pg.wait_for_function(f"{list(PAINTED_ROSTER)!r}.every(p => VoolCompanionWorld.ready(p))", timeout=20000)
    pg.evaluate("""() => {
      // One frame on a canvas; returns the canvas pixels for diffing (badge corner left out, so a
      // move is measured on the body, not on the work sign).
      window.frameOn = (c, pet, state, activity, elapsed, now, reduced) => {
        const g = c.getContext('2d');
        VoolCompanionWorld.draw(g, state, 0, {character: pet, activity, elapsed, now, reduced});
        g.setTransform(1, 0, 0, 1, 0, 0);
        const d = g.getImageData(0, 0, c.width, c.height).data, w = c.width, cut = Math.floor(w * 37 / 48);
        const out = new Uint8Array(w * w);
        for (let y = 0; y < w; y++) for (let x = 0; x < w; x++) {
          if (x >= cut && y >= cut) continue;
          const i = (y * w + x) * 4; out[y * w + x] = (d[i] * 0.3 + d[i + 1] * 0.59 + d[i + 2] * 0.11) * d[i + 3] / 255;
        }
        return out;
      };
      // Plays a script of [state, activity, from ms, to ms, elapsed at start] on ONE canvas at
      // 30 frames a second and returns every frame's change from the one before, and the
      // postures it went through.
      window.play = (pet, script) => {
        const c = VoolCompanionWorld.fit(document.createElement('canvas'), 144);
        let prev = null; const diffs = [], postures = [];
        const end = script[script.length - 1][3];
        for (let k = 0; k * 1000 / 30 < end; k++) {   // one frame every 1/30 s, each at its own time
          const now = k * 1000 / 30;
          const [state, act, t0, t1, el0] = script.find(s => now >= s[2] && now < s[3]);
          {
            const cur = frameOn(c, pet, state, act, el0 + (now - t0), now, false);
            if (prev) { let s = 0; for (let i = 0; i < cur.length; i++) s += Math.abs(cur[i] - prev[i]); diffs.push(s / cur.length); }
            prev = cur;
            const p = VoolCompanionWorld.postureOf(c);
            const name = p.to ? p.posture + '>' + p.to : p.posture;
            if (postures[postures.length - 1] !== name) postures.push(name);
          }
        }
        return {diffs, postures};
      };
      window.sig = (pet, state, activity, elapsed) => {
        const c = VoolCompanionWorld.fit(document.createElement('canvas'), 144);
        const px = frameOn(c, pet, state, activity, elapsed, 5000 + elapsed, false);
        let h = 2166136261; for (let i = 0; i < px.length; i++) h = Math.imul(h ^ px[i], 16777619);
        return h >>> 0;
      };
    }""")
    yield pg, errors
    pg.close()
    browser.close()
    manager.stop()


def test_every_pet_is_cut_into_parts_that_are_small_webp():
    assert set(RIG_ART) == set(PAINTED_ROSTER) == set(RIG_META)
    total = 0
    for pet, parts in RIG_ART.items():
        assert set(parts) == set(RIG_META[pet]["parts"]), pet
        assert "torso" in parts and len(parts) >= 3, f"{pet} moves more than one piece"
        for name, b64 in parts.items():
            raw = base64.b64decode(b64)
            assert raw[:4] == b"RIFF" and raw[8:12] == b"WEBP", (pet, name)
            total += len(b64)
    assert total < 560_000, f"the parts add {total} bytes of base64 to every page"


def test_resting_sits_then_lies_down_and_work_gets_it_up_again_without_a_jump(page):
    """Idle for long enough walks stand > sit > lie; a task walks lie > sit > stand. No frame in
    either walk changes much more than the frames on both sides of it (a jump)."""
    pg, errors = page
    for pet in PAINTED_ROSTER:
        out = pg.evaluate("a => play(a[0], a[1])", [pet, [
            ["idle", None, 0, 1500, 0],
            ["idle", None, 1500, 6000, 95000],      # rested long enough to lie down
            ["tool", "code", 6000, 10000, 0],       # work arrives: it gets up first
        ]])
        p = out["postures"]
        steps = [x for x in p if ">" in x]
        assert steps == ["stand>sit", "sit>lie", "lie>sit", "sit>stand"], f"{pet}: {p}"
        assert "lie" in p and p[-1] == "stand", f"{pet}: {p}"
        d = out["diffs"]
        jumps = [(i, round(d[i], 2)) for i in range(1, len(d) - 1)
                 if d[i] > 1.5 and d[i] > 3 * max(d[i - 1], d[i + 1], 0.2)]
        assert not jumps, f"{pet} jumps at frames {jumps}"
    assert not errors, errors


def test_switching_work_cross_fades_instead_of_snapping(page):
    pg, _ = page
    for pet in PAINTED_ROSTER:
        script = [["thinking", None, 0, 1000, 0]]
        t = 1000
        for act in ACTIVITIES + ["read"]:
            script.append(["tool", act, t, t + 700, 0]); t += 700
        script += [["approval", None, t, t + 900, 0], ["success", None, t + 900, t + 2200, 0], ["idle", None, t + 2200, t + 3000, 0]]
        d = pg.evaluate("a => play(a[0], a[1])", [pet, script])["diffs"]
        jumps = [(i, round(d[i], 2)) for i in range(1, len(d) - 1)
                 if d[i] > 1.5 and d[i] > 3 * max(d[i - 1], d[i + 1], 0.2)]
        assert not jumps, f"{pet} snaps at frames {jumps}"


def test_each_kind_of_work_has_its_own_move_on_every_pet(page):
    pg, _ = page
    for pet in PAINTED_ROSTER:
        sigs = {pg.evaluate("a => sig(a[0], 'tool', a[1], 700)", [pet, act]) for act in ACTIVITIES}
        assert len(sigs) == len(ACTIVITIES), f"{pet}: {len(sigs)} different moves for {len(ACTIVITIES)} kinds of work"
