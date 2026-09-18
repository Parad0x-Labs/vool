from __future__ import annotations

from core.demo_planner import (
    DemoBrief,
    Feature,
    _timeline,
    plan_image_set,
    plan_video_demo,
)

_BRIEF = DemoBrief(
    product_name="VOOL",
    tagline="An honest local AI agent that owns its own keys",
    features=(
        Feature("Local-first", "runs models on your own machine, no cloud"),
        Feature("Honest receipts", "signed, offline-verifiable proofs of what it did"),
        Feature("Own your wallet", "a self-custody Solana wallet, no middleman"),
    ),
    presenter="woman",
    total_seconds=30,
)


def test_timeline_sums_and_is_contiguous() -> None:
    spans = _timeline(30, [1.0, 1.3, 1.5, 1.5, 1.5, 1.3])
    assert spans[0][0] == 0
    assert spans[-1][1] == 30
    assert all(b - a >= 2 for a, b in spans)              # every segment >= 2s
    assert all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1))  # no gaps/overlaps


def test_timeline_handles_tiny_total() -> None:
    spans = _timeline(4, [1, 1, 1, 1])   # asked for 4s across 4 segments -> min 2s each floors it up
    assert all(b - a >= 2 for a, b in spans)
    assert spans[0][0] == 0
    assert all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1))  # contiguous


def test_plan_structure_hook_features_outro() -> None:
    plan = plan_video_demo(_BRIEF)
    assert len(plan.shots) == 2 + 3 + 1                   # hook + intro + 3 features + outro
    beats = [s.beat for s in plan.shots]
    assert beats[0] == "hook" and beats[1] == "intro" and beats[-1] == "outro"
    assert beats[2:5] == ["feature", "feature", "feature"]
    assert plan.total_seconds == 30


def test_plan_is_split_in_seconds_and_contiguous() -> None:
    plan = plan_video_demo(_BRIEF)
    assert plan.shots[0].start_s == 0
    for a, b in zip(plan.shots, plan.shots[1:], strict=False):
        assert a.end_s == b.start_s                        # contiguous timeline
        assert a.duration_s >= 2


def test_shot_prompts_are_generation_ready() -> None:
    plan = plan_video_demo(_BRIEF)
    hook = plan.shots[0]
    assert hook.subtitle == "VOOL"
    feature = plan.shots[2]
    assert "woman presenter" in feature.generation_prompt      # presenter honored
    assert "Negative prompt:" in feature.generation_prompt     # failure-prevention present
    assert feature.subtitle in feature.generation_prompt       # subtitle burned into the prompt


def test_no_features_falls_back_to_overview() -> None:
    plan = plan_video_demo(DemoBrief(product_name="X", tagline="does things"))
    assert len(plan.shots) == 2 + 1 + 1                        # one synthesized Overview feature
    assert any(s.beat == "feature" for s in plan.shots)


def test_image_set_hero_plus_features() -> None:
    images = plan_image_set(_BRIEF)
    assert len(images) == 1 + 3                                 # hero + one per feature
    assert images[0].label == "hero"
    assert "Negative prompt:" in images[1].generation_prompt


def test_shots_carry_voiceover_and_narration_script() -> None:
    plan = plan_video_demo(_BRIEF)
    assert all(s.voiceover for s in plan.shots)             # every shot narrated
    assert plan.shots[0].voiceover == "Meet VOOL."
    script = plan.narration_script()
    assert "Meet VOOL." in script and "Get started today." in script
    assert "Voiceover" in plan.shots[2].generation_prompt   # VO folded into the shot prompt too


def test_storyboard_has_title_music_and_script() -> None:
    plan = plan_video_demo(_BRIEF)
    md = plan.to_markdown()
    assert "Music:" in md and "Voiceover script" in md and "Voiceover:" in md
    assert plan.title.startswith("VOOL")
    d = plan.to_dict()
    assert d["narration_script"] and d["shots"][0]["voiceover"] == "Meet VOOL."


def test_retime_on_empty_plan_is_safe() -> None:
    from core.demo_planner import DemoPlan, retime
    out = retime(DemoPlan(brief=DemoBrief(product_name="X"), shots=[]), 30)
    assert out.shots == [] and out.total_seconds == 0


def test_plan_serializes() -> None:
    plan = plan_video_demo(_BRIEF)
    d = plan.to_dict()
    assert d["product"] == "VOOL" and d["total_seconds"] == 30
    assert d["shots"][0]["duration_s"] >= 2
    md = plan.to_markdown()
    assert "Demo video plan - VOOL" in md and "Shot 0" in md
