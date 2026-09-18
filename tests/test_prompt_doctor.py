from __future__ import annotations

import json

from core.prompt_doctor import (
    DIMENSIONS,
    build_scoring_prompt,
    doctor_prompt,
    parse_scores,
    verdict,
    weighted_overall,
)

_ALL_KEYS = [d.key for d in DIMENSIONS]


def scores_json(value: float, **overrides) -> str:
    d = {k: value for k in _ALL_KEYS}
    d.update(overrides)
    return json.dumps(d)


class _Stub:
    """Sequential model_client stub: returns canned replies in order, records prompts."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


# --- scoring math ---

def test_weighted_overall_equal_scores() -> None:
    assert weighted_overall({k: 8 for k in _ALL_KEYS}, "video") == 8.0


def test_gate_below_six_caps_overall() -> None:
    s = {k: 9 for k in _ALL_KEYS}
    s["model_compatibility"] = 5  # a gate below 6
    assert weighted_overall(s, "video") == 6.0


def test_verdict_ship_and_no_ship() -> None:
    assert verdict({k: 9 for k in _ALL_KEYS}, "video")["ship"] is True
    v = verdict({k: 5 for k in _ALL_KEYS}, "video")
    assert v["ship"] is False and v["zone"] == "rewrite_all_sub6"


def test_unsafe_safety_blocks() -> None:
    v = verdict(scores := {**{k: 9 for k in _ALL_KEYS}, "safety": 1}, "video")
    assert v["blocked"] is True and v["ship"] is False and v["zone"] == "blocked_unsafe"
    assert scores["safety"] == 1  # explicit adult content would score high here; minors do not


def test_still_drops_motion_and_sound() -> None:
    # scene_motion tanked: blocks a VIDEO prompt but not a STILL (dropped for images).
    s = {**{k: 9 for k in _ALL_KEYS}, "scene_motion": 1, "sound_voice_usefulness": 1}
    assert verdict(s, "video")["ship"] is False
    assert verdict(s, "image")["ship"] is True


def test_originality_reweighted_for_stills() -> None:
    # originality is weighted higher for stills, so a low originality bites harder on an image.
    s = {**{k: 9 for k in _ALL_KEYS}, "originality": 5}
    assert weighted_overall(s, "image") < weighted_overall(s, "video")


# --- parsing ---

def test_parse_scores_variants() -> None:
    assert parse_scores('{"visual_clarity": 9, "bogus": 3}') == {"visual_clarity": 9.0}
    assert parse_scores('```json\n{"genre_fit": 7}\n```') == {"genre_fit": 7.0}
    assert parse_scores('here are scores {"safety": 10} ok') == {"safety": 10.0}
    assert parse_scores("not json") == {}
    # clamps to 1..10
    assert parse_scores('{"visual_clarity": 15, "originality": 0}') == {"visual_clarity": 10.0, "originality": 1.0}


def test_scoring_prompt_lists_dimensions_and_medium() -> None:
    p = build_scoring_prompt("a dragon", "video")
    assert "visual_clarity" in p and "safety" in p and "video" in p
    assert "scene_motion" not in build_scoring_prompt("a portrait", "image")  # dropped for stills


# --- the loop ---

def test_ships_on_first_pass_without_rewrite() -> None:
    stub = _Stub([scores_json(9)])
    out = doctor_prompt("great prompt", model_client=stub, medium="video")
    assert out["rounds"] == 1
    assert out["verdict"]["ship"] is True
    assert out["final_prompt"] == "great prompt"
    assert len(stub.prompts) == 1  # scored once, never rewritten


def test_rewrites_then_ships() -> None:
    stub = _Stub([scores_json(5), "A REWRITTEN, SHARPER PROMPT", scores_json(9)])
    out = doctor_prompt("weak prompt", model_client=stub, medium="video")
    assert out["rounds"] == 2
    assert out["final_prompt"] == "A REWRITTEN, SHARPER PROMPT"
    assert out["verdict"]["ship"] is True


def test_caps_at_two_rewrites() -> None:
    stub = _Stub([scores_json(5), "r1", scores_json(5), "r2", scores_json(5)])
    out = doctor_prompt("stubborn", model_client=stub, medium="video", max_rewrites=2)
    assert out["rounds"] == 3  # initial + 2 rewrites
    assert out["verdict"]["ship"] is False
    assert "rewrite" in out["note"].lower()


def test_unsafe_block_short_circuits_and_withholds_output() -> None:
    stub = _Stub([scores_json(9, safety=1)])
    out = doctor_prompt("unsafe idea", model_client=stub, medium="video")
    assert out["blocked"] is True
    assert out["final_prompt"] == ""  # not shipped
    assert out["rounds"] == 1
