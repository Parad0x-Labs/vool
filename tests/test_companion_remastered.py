"""The remastered pets: ten extra companions beside the legacy ten.

They are drawn through the real shared renderer (headless), so these checks cover what the app
draws: separate ids, the legacy roster untouched, a distinct picture per pet, a pose for every
state and activity that actually changes the drawing, and a still picture under reduced motion.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from core.companion_art_fragment import COMPANION_ROSTER
from core.companion_remaster_fragment import REMASTER_JS, REMASTER_ROSTER
from core.companion_world_fragment import COMPANION_WORLD_JS, normalise_companion_payload

ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "fixtures" / "companion" / "render_pets.js"
NODE = shutil.which("node")
requires_node = pytest.mark.skipif(not NODE, reason="node is required to drive the renderer")

LEGACY = ["beetle", "raven", "golem", "tide", "spark", "rascal", "prime", "prism", "veil", "ember"]
STATES = ["idle", "starting", "thinking", "tool", "waiting", "approval", "retry",
          "success", "failure", "cancelled", "unknown"]
ACTIVITIES = ["read", "search", "dig", "code", "exec", "test", "watch"]


def _render(tmp_path, states, pets, elapsed=1400, activity="", reduced=False):
    world = tmp_path / "world.js"
    world.write_text(COMPANION_WORLD_JS)
    out = tmp_path / "frames.json"
    cmd = [NODE, str(HARNESS), str(world), ",".join(states), str(out), str(elapsed),
           ",".join(pets), activity]
    if reduced:
        cmd.append("reduced")
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.read_text())


def _sig(grid):
    return tuple(tuple(row) for row in grid)


def test_roster_is_ten_separate_ids_and_legacy_is_unchanged():
    assert list(COMPANION_ROSTER) == LEGACY
    assert len(REMASTER_ROSTER) == 10 == len(set(REMASTER_ROSTER))
    for pet in REMASTER_ROSTER:
        assert re.fullmatch(r"rm_[a-z]+", pet)
        assert pet not in COMPANION_ROSTER
    assert [p[3:] for p in REMASTER_ROSTER] == LEGACY, "one remaster per legacy pet, same order"


def test_payload_accepts_remastered_ids_and_rejects_unknown_ones():
    for pet in REMASTER_ROSTER:
        assert normalise_companion_payload({"character": pet})["character"] == pet
    for pet in LEGACY:
        assert normalise_companion_payload({"character": pet})["character"] == pet
    assert normalise_companion_payload({"character": "rm_nothing"})["character"] == "spark"
    assert normalise_companion_payload({"character": "RM_RAVEN"})["character"] == "rm_raven"


def test_remaster_source_is_self_contained_and_marks_the_group():
    assert "A_PETS[id]=Object.assign({remaster:true}" in REMASTER_JS
    assert "remastered:!!meta.remaster" in COMPANION_WORLD_JS
    # No outbound fetch, storage or DOM access: it only draws.
    for banned in ("fetch(", "XMLHttpRequest", "localStorage", "document.", "innerHTML"):
        assert banned not in REMASTER_JS


@requires_node
def test_every_remastered_pet_draws_a_distinct_picture(tmp_path):
    frames = _render(tmp_path, ["idle"], list(REMASTER_ROSTER) + LEGACY)
    seen = {}
    for pet in list(REMASTER_ROSTER) + LEGACY:
        grid = frames[f"{pet}/idle"]
        pts = [(x, y) for y in range(48) for x in range(48) if grid[y][x]]
        assert pts, f"{pet} drew nothing"
        assert len(pts) / (48 * 48) < 0.6, f"{pet} fills the grid"
        sig = _sig(grid)
        assert sig not in seen, f"{pet} is identical to {seen.get(sig)}"
        seen[sig] = pet
    assert len(seen) == 20


@requires_node
def test_every_state_has_a_pose_that_changes_the_drawing(tmp_path):
    frames = _render(tmp_path, STATES, list(REMASTER_ROSTER))
    for pet in REMASTER_ROSTER:
        idle = _sig(frames[f"{pet}/idle"])
        for state in STATES[1:]:
            grid = frames[f"{pet}/{state}"]
            assert any(v for row in grid for v in row), f"{pet}/{state} drew nothing"
            if state in {"thinking", "retry"}:
                continue  # same pose as 'starting' by the shared pose law
            assert _sig(grid) != idle, f"{pet}/{state} looks the same as idle"


@requires_node
def test_failure_and_success_read_differently_from_each_other_and_from_work(tmp_path):
    frames = _render(tmp_path, ["starting", "success", "failure", "approval"], list(REMASTER_ROSTER))
    for pet in REMASTER_ROSTER:
        sigs = {s: _sig(frames[f"{pet}/{s}"]) for s in ["starting", "success", "failure", "approval"]}
        assert len(set(sigs.values())) == 4, f"{pet}: two states share one picture"


@requires_node
def test_each_activity_shows_its_own_prop(tmp_path):
    for pet in REMASTER_ROSTER:
        base = _sig(_render(tmp_path, ["tool"], [pet])[f"{pet}/tool"])
        seen = {}
        for act in ACTIVITIES:
            sig = _sig(_render(tmp_path, ["tool"], [pet], activity=act)[f"{pet}/tool"])
            assert sig != base, f"{pet}: activity {act} changes nothing"
            seen.setdefault(sig, []).append(act)
        assert len(seen) >= 5, f"{pet}: activities collapse into {len(seen)} pictures"


@requires_node
def test_reduced_motion_holds_one_still_picture(tmp_path):
    for state in ["idle", "starting", "tool", "waiting"]:
        a = _render(tmp_path, [state], list(REMASTER_ROSTER), elapsed=100, reduced=True)
        b = _render(tmp_path, [state], list(REMASTER_ROSTER), elapsed=4300, reduced=True)
        for pet in REMASTER_ROSTER:
            assert _sig(a[f"{pet}/{state}"]) == _sig(b[f"{pet}/{state}"]), f"{pet}/{state} moves under reduced motion"


@requires_node
def test_drawn_colours_are_plain_hex_or_rgba(tmp_path):
    frames = _render(tmp_path, ["idle", "success", "failure"], list(REMASTER_ROSTER))
    for key, grid in frames.items():
        for row in grid:
            for c in row:
                assert c is None or re.fullmatch(r"#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}|rgba?\([\d., ]+\)", c), (key, c)


@requires_node
def test_the_raven_looks_left(tmp_path):
    grid = _render(tmp_path, ["idle"], ["rm_raven"])["rm_raven/idle"]
    beak = [(x, y) for y in range(48) for x in range(48)
            if grid[y][x] and grid[y][x].lower() in {"#e0a64c", "#ffd98a", "#a8722a"}]
    assert beak and sum(x for x, _ in beak) / len(beak) < 24
