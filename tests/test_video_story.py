from __future__ import annotations

import core.demo_source as demo_source
import core.policy_engine as policy_engine
from core.demo_planner import DemoBrief, Feature
from core.runtime_execution_tools import _demo_plan
from core.video_story import build_video_story_pack, build_video_story_revise_prompt, story_from_model

_BRIEF = DemoBrief(
    product_name="VOOL",
    tagline="a private local AI agent that proves what it did",
    features=(
        Feature("Honest receipts", "signed, offline-verifiable proofs"),
        Feature("Local-first", "runs models on your own machine"),
    ),
    presenter="woman",
    total_seconds=100,
)

_README = (
    "# VOOL\n\nhonest local AI agent that owns its keys\n\n"
    "## Features\n"
    "- Local-first - runs models on your own machine\n"
    "- Honest receipts - signed, offline-verifiable proofs\n"
)


# --- the writing pack ---

def test_pack_has_real_features_arc_and_format() -> None:
    pack = build_video_story_pack(_BRIEF)
    assert pack.product == "VOOL" and pack.total_seconds == 100
    assert [f["name"] for f in pack.features] == ["Honest receipts", "Local-first"]
    assert pack.arc[:3] == ["hook", "problem", "reveal"]
    assert pack.arc.count("feature") == 2 and pack.arc[-1] == "cta"
    ins = pack.instruction
    assert "VOOL" in ins and "Honest receipts" in ins and "Local-first" in ins  # real features woven in
    assert "SCENE" in ins and "SHE SAYS" in ins                                   # scene + dialogue format
    assert "not a slideshow" in ins.lower() and "HONESTY" in ins
    assert "woman presenter" in pack.presenter                                    # preset expanded
    # hardened non-negotiable rules (make even a weak model behave)
    assert "NON-NEGOTIABLE" in ins
    assert "add up to exactly 100s" in ins                                        # timing sum forced
    assert "EVERY shot has a spoken" in ins                                       # no empty hook/CTA
    assert "CAMERA move" in ins and "on-screen visual" in ins                     # render-ready scenes
    assert "PROOF beat is the emotional PEAK" in ins                              # peak enforced


def test_arc_caps_feature_beats_for_a_feature_heavy_product() -> None:
    from core.demo_planner import DemoBrief, Feature
    many = DemoBrief(product_name="X", tagline="t",
                     features=tuple(Feature(f"f{i}", "b") for i in range(6)), total_seconds=100)
    pack = build_video_story_pack(many)
    assert pack.arc.count("feature") == 4                    # 6 features -> capped at 4 beats
    assert len(pack.arc) == 9                                # hook, problem, reveal, 4x feature, proof, cta


def test_revise_prompt_targets_the_failure_modes() -> None:
    pack = build_video_story_pack(_BRIEF, total_seconds=100)
    rev = build_video_story_revise_prompt(pack, "Logline: x\nSHOT 1 [4s] ...")
    assert "DRAFT TO REVISE:" in rev and "SHOT 1 [4s]" in rev              # the draft is embedded
    assert "add up to exactly 100s" in rev                                 # timing fix
    assert "EVERY shot needs a spoken" in rev and "hook" in rev and "CTA" in rev
    assert "render-ready" in rev and "emotional peak" in rev


def test_pack_presenter_freeform_is_used_verbatim() -> None:
    pack = build_video_story_pack(_BRIEF, presenter="a flirty geek-girl host in cat-eye glasses")
    assert pack.presenter == "a flirty geek-girl host in cat-eye glasses"
    assert "a flirty geek-girl host in cat-eye glasses" in pack.instruction


def test_pack_seconds_override() -> None:
    pack = build_video_story_pack(_BRIEF, total_seconds=115)
    assert pack.total_seconds == 115 and "exactly 115s" in pack.instruction


def test_pack_locks_host_and_world_from_saved_set(tmp_path, monkeypatch) -> None:
    import core.creative_set as cs
    from core.creative_set import Character, CreativeSet
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "sets.json")
    cs.save_set(CreativeSet(
        name="VOOL_HOST",
        characters=(Character(name="Nova", appearance="a flirty geek-girl, cat-eye glasses",
                              wardrobe="a plaid overshirt", voice="warm and playful"),),
        setting="a neon dev studio", style="cinematic tech-forward", palette="teal and amber",
        story="VOOL proves what it did", negative="childlike, underage"))
    pack = build_video_story_pack(_BRIEF, set_name="VOOL_HOST")
    assert "Nova" in pack.presenter and "cat-eye glasses" in pack.presenter
    assert "wearing a plaid overshirt" in pack.presenter                 # wardrobe locked into the host
    ins = pack.instruction
    assert "LOCKED SET" in ins and "neon dev studio" in ins and "teal and amber" in ins
    assert "Nova's voice: warm and playful" in ins and "childlike, underage" in ins


def test_pack_missing_set_falls_back_to_presenter(tmp_path, monkeypatch) -> None:
    import core.creative_set as cs
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "none.json")
    pack = build_video_story_pack(_BRIEF, presenter="a geek-girl host", set_name="GHOST")
    assert pack.presenter == "a geek-girl host" and "LOCKED SET" not in pack.instruction


# --- normalizing a model's output ---

def test_story_from_model_dict_form_drops_empty_and_coerces() -> None:
    data = {
        "logline": "a story",
        "shots": [
            {"n": 1, "seconds": 10, "beat": "hook", "scene": "dim office", "dialogue": "be honest", "sound": "drone"},
            {"seconds": "bad", "beat": "empty", "scene": "", "dialogue": ""},   # dropped: no scene/dialogue
            {"n": 2, "seconds": 12.5, "beat": "cta", "scene": "brand frame", "dialogue": "that's it"},
        ],
    }
    script = story_from_model("VOOL", "tag", "woman", 100, data)
    assert script.logline == "a story"
    assert len(script.shots) == 2                       # the empty shot is dropped
    assert script.shots[0].scene == "dim office"
    assert script.shots[1].seconds == 12.5


def test_story_from_model_accepts_bare_list() -> None:
    script = story_from_model("P", "t", "girl", 30, [{"scene": "x", "dialogue": "y"}])
    assert len(script.shots) == 1 and script.shots[0].n == 1


def test_to_plaintext_and_narration() -> None:
    script = story_from_model("VOOL", "t", "woman", 50, {"shots": [
        {"n": 1, "seconds": 10, "beat": "hook", "scene": "S1", "dialogue": "D1", "sound": "boom"},
        {"n": 2, "seconds": 8, "beat": "cta", "scene": "S2", "dialogue": "D2", "sound": ""},
    ]})
    txt = script.to_plaintext()
    assert "SHOT 1   [10s]   HOOK" in txt and "SCENE (video / background):" in txt
    assert "SHE SAYS (on camera):" in txt and '"D1"' in txt and "SOUND: boom" in txt
    assert script.narration_only() == "D1 D2"


# --- the demo.plan handler wiring ---

def test_demo_plan_video_story_format(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    monkeypatch.setattr(demo_source, "fetch_readme", lambda o, r, fetcher=None: _README)
    res = _demo_plan({"url": "github.com/o/r", "presenter": "a flirty geek-girl host",
                      "seconds": 110, "format": "video_story"})
    assert res.ok and res.status == "ok"
    assert "SHE SAYS" in res.response_text and "SCENE" in res.response_text       # the writing pack
    pack = res.details["video_story_pack"]
    assert pack["total_seconds"] == 110 and pack["presenter"] == "a flirty geek-girl host"
    assert res.details["observation"]["format"] == "video_story"


def test_demo_plan_slideshow_still_default(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    monkeypatch.setattr(demo_source, "fetch_readme", lambda o, r, fetcher=None: _README)
    res = _demo_plan({"url": "github.com/o/r"})                                    # no format -> slideshow
    assert res.ok and "plan" in res.details and "images" in res.details
    assert "video_story_pack" not in res.details
