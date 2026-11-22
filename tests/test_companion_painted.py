"""The painted pets (owner's remaster, 2026-11-22): ten pets, one look each, drawn from paintings.

Everything here runs the real shared renderer in a real browser, because the paintings are
decoded images: a node stub cannot show whether the art loads, moves, or falls back.
"""

from __future__ import annotations

import base64

import pytest

from core.companion_painted_fragment import PAINTED_ALIASES, PAINTED_ROSTER, resolve_pet_id
from core.companion_pet_art import PET_ART
from core.companion_world_fragment import COMPANION_WORLD_JS, normalise_companion_payload
from tests.served_browser import launch_chromium

STATES = ["idle", "starting", "thinking", "tool", "waiting", "approval", "retry",
          "success", "failure", "cancelled", "unknown"]


@pytest.fixture(scope="module")
def page():
    manager, browser = launch_chromium()
    pg = browser.new_page(device_scale_factor=2)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.set_content("<html><body></body><script>" + COMPANION_WORLD_JS + "</script></html>")
    pg.wait_for_function(f"{list(PAINTED_ROSTER)!r}.every(p => VoolCompanionWorld.ready(p))", timeout=15000)
    pg.evaluate("""() => {
      window.snap = (pet, state, elapsed, activity, reduced) => {
        const c = VoolCompanionWorld.fit(document.createElement('canvas'), 144);
        const g = c.getContext('2d');
        VoolCompanionWorld.draw(g, state, 0, {character: pet, elapsed, activity, reduced});
        g.setTransform(1, 0, 0, 1, 0, 0);
        const d = g.getImageData(0, 0, c.width, c.height).data;
        let h = 2166136261, ink = 0;
        for (let i = 0; i < d.length; i += 4) { if (d[i + 3] > 8) ink++; h = Math.imul(h ^ (d[i] + d[i + 1] * 3 + d[i + 3] * 7), 16777619); }
        return {w: c.width, ink, sig: h >>> 0};
      };
    }""")
    yield pg, errors
    pg.close()
    browser.close()
    manager.stop()


def test_the_roster_is_the_ten_the_owner_kept_and_retired_looks_map_to_their_creature():
    assert list(PAINTED_ROSTER) == ["beetle", "raven", "golem", "tide", "spark",
                                    "rascal", "prime", "prism", "veil", "ember"]
    assert set(PET_ART) == set(PAINTED_ROSTER)
    for retired, pet in PAINTED_ALIASES.items():
        assert retired == "rm_" + pet and resolve_pet_id(retired) == pet
        assert normalise_companion_payload({"character": retired})["character"] == pet
    assert resolve_pet_id("<script>") == "spark"


def test_every_painting_is_a_small_webp_cut_out():
    for pet, b64 in PET_ART.items():
        raw = base64.b64decode(b64)
        assert raw[:4] == b"RIFF" and raw[8:12] == b"WEBP", pet
        assert len(raw) < 48_000, f"{pet} painting is {len(raw)} bytes"
    total = sum(len(b) for b in PET_ART.values())
    assert total < 520_000, f"all ten paintings add {total} bytes of base64 to every page"


def test_every_pet_decodes_and_draws_its_own_picture_sharply(page):
    pg, errors = page
    seen = {}
    for pet in PAINTED_ROSTER:
        shot = pg.evaluate("p => snap(p, 'idle', 0, null, true)", pet)
        assert shot["w"] == 288, "a 144 px pet is backed at 2x on a 2x screen"
        assert shot["ink"] > 288 * 288 * 0.2, f"{pet} draws a body, not a speck"
        assert shot["ink"] < 288 * 288 * 0.9, f"{pet} is a cut-out, not a filled tile"
        seen[shot["sig"]] = pet
    assert len(seen) == 10, "ten different pictures"
    assert not errors, errors


def test_every_state_moves_the_picture_and_reduced_motion_holds_still(page):
    pg, _ = page
    for pet in PAINTED_ROSTER:
        idle = pg.evaluate("p => snap(p, 'idle', 0, null, true).sig", pet)
        for state in STATES[1:]:
            elapsed = 300 if state == "success" else 1200
            shot = pg.evaluate("a => snap(a[0], a[1], a[2], 'search', false).sig", [pet, state, elapsed])
            assert shot != idle, f"{pet}/{state} looks the same as resting"
        # Alive at rest: two moments of idle differ; under reduced motion they do not.
        a = pg.evaluate("p => snap(p, 'idle', 400, null, false).sig", pet)
        b = pg.evaluate("p => snap(p, 'idle', 1700, null, false).sig", pet)
        assert a != b, f"{pet} does not move at rest"
        ra = pg.evaluate("p => snap(p, 'idle', 400, null, true).sig", pet)
        rb = pg.evaluate("p => snap(p, 'idle', 1700, null, true).sig", pet)
        assert ra == rb, f"{pet} moves under reduced motion"


def test_each_kind_of_work_shows_its_own_sign(page):
    pg, _ = page
    sigs = {pg.evaluate("a => snap('golem', 'tool', 0, a, true).sig", act)
            for act in ["read", "search", "dig", "code", "exec", "test", "watch"]}
    assert len(sigs) == 7


def test_a_painting_that_cannot_be_decoded_falls_back_to_the_same_creature(page):
    """A webview that cannot read WebP still shows the pet (its old drawing), never a hole."""
    pg, _ = page
    out = pg.evaluate("""() => {
      const c = VoolCompanionWorld.fit(document.createElement('canvas'), 144), g = c.getContext('2d');
      const keep = VP_IMG.golem;
      VP_IMG.golem = new Image();  // never loaded: what a webview without WebP leaves behind
      let ink = false, painted = null, ready = null;
      try {
        ready = VoolCompanionWorld.ready('golem');
        painted = VoolCompanionWorld.drawPainted(g, 'golem', 'idle', null, 0, {});
        VoolCompanionWorld.draw(g, 'idle', 0, {character: 'rm_golem', elapsed: 0});
        g.setTransform(1, 0, 0, 1, 0, 0);
        ink = g.getImageData(0, 0, c.width, c.height).data.some(v => v);
      } finally { VP_IMG.golem = keep; }
      return {ready, painted, ink, legacy: VoolCompanionWorld.characters.golem.legacy};
    }""")
    assert out["ready"] is False and out["painted"] is False
    assert out["ink"], "the pet must still be drawn"
    assert out["legacy"] == "rm_golem", "golem falls back to its own kept look"


def test_one_frame_is_cheap(page):
    pg, _ = page
    worst = pg.evaluate("""() => {
      const c = VoolCompanionWorld.fit(document.createElement('canvas'), 144), g = c.getContext('2d');
      const pets = Object.keys(VoolCompanionWorld.painted), out = {};
      for (const p of pets) {
        for (let t = 0; t < 600; t += 33) VoolCompanionWorld.draw(g, 'tool', 0, {character: p, elapsed: t, activity: 'code'});
        const s = performance.now();
        for (let t = 0; t < 3000; t += 33) VoolCompanionWorld.draw(g, 'tool', 0, {character: p, elapsed: t, activity: 'code'});
        out[p] = (performance.now() - s) / 91;
      }
      return out;
    }""")
    assert max(worst.values()) < 8.0, worst
