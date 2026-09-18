from __future__ import annotations

import pytest

from core.demo_plan_edit import (
    apply_edits,
    apply_op,
    move_shot,
    parse_edit_instruction,
    remove_shot,
    set_duration,
    set_field,
    set_presenter,
)
from core.demo_planner import DemoBrief, Feature, plan_video_demo, retime

_BRIEF = DemoBrief(
    product_name="VOOL", tagline="honest local agent",
    features=(Feature("Local-first", "runs on your machine"),
              Feature("Receipts", "signed proofs"),
              Feature("Wallet", "own your keys")),
    presenter="woman", total_seconds=30,
)


def _plan():
    return plan_video_demo(_BRIEF)  # 6 shots: hook, intro, 3 features, outro (indices 0..5)


def _contiguous(plan) -> bool:
    return plan.shots[0].start_s == 0 and all(
        a.end_s == b.start_s for a, b in zip(plan.shots, plan.shots[1:], strict=False)
    )


def test_set_field_updates_subtitle_and_prompt() -> None:
    out = set_field(_plan(), 2, "subtitle", "blazing fast")
    assert out.shots[2].subtitle == "blazing fast"
    assert "blazing fast" in out.shots[2].generation_prompt


def test_set_duration_relayouts_contiguously() -> None:
    plan = _plan()
    orig_total = plan.total_seconds
    out = set_duration(plan, 2, 10)
    assert out.shots[2].duration_s == 10
    assert _contiguous(out)
    assert out.total_seconds == orig_total - plan.shots[2].duration_s + 10


def test_remove_shot_renumbers_and_relayouts() -> None:
    out = remove_shot(_plan(), 5)               # drop the outro
    assert len(out.shots) == 5
    assert [s.index for s in out.shots] == [0, 1, 2, 3, 4]
    assert _contiguous(out)
    assert not any(s.beat == "outro" for s in out.shots)


def test_remove_shot_refuses_to_empty() -> None:
    single = plan_video_demo(DemoBrief(product_name="X", tagline="y"))
    while len(single.shots) > 1:
        single = remove_shot(single, len(single.shots) - 1)
    assert remove_shot(single, 0) is single or len(single.shots) == 1


def test_move_shot_reorders() -> None:
    plan = _plan()
    label = plan.shots[2].subtitle
    out = move_shot(plan, 2, 4)
    assert out.shots[4].subtitle == label
    assert _contiguous(out)


def test_set_presenter_recasts_all_prompts() -> None:
    out = set_presenter(_plan(), "man")
    assert out.brief.presenter == "man"
    assert all("man presenter" in s.generation_prompt for s in out.shots)


def test_retime_changes_total_and_keeps_contiguity() -> None:
    out = retime(_plan(), 60)
    assert out.total_seconds == 60
    assert _contiguous(out)


def test_apply_op_dispatch() -> None:
    plan = _plan()
    assert apply_op(plan, {"op": "retime", "seconds": 45}).total_seconds == 45
    assert apply_op(plan, {"op": "nonsense"}) is plan
    assert apply_op(plan, "not a dict") is plan


@pytest.mark.parametrize(
    "text, expected",
    [
        ("make it 45 seconds", {"op": "retime", "seconds": 45}),
        ("use a man presenter", {"op": "set_presenter", "value": "man"}),
        ("remove shot 4", {"op": "remove_shot", "shot": 4}),
        ("shot 3 to 8 seconds", {"op": "set_duration", "shot": 3, "seconds": 8}),
    ],
)
def test_parse_edit_instruction_common(text, expected) -> None:
    assert parse_edit_instruction(text, _plan()) == expected


def test_parse_drop_the_outro_resolves_beat() -> None:
    op = parse_edit_instruction("drop the outro", _plan())
    assert op == {"op": "remove_shot", "shot": 5}


def test_parse_subtitle_preserves_case() -> None:
    op = parse_edit_instruction("set shot 2 subtitle to Blazing Fast", _plan())
    assert op == {"op": "set_field", "shot": 2, "field": "subtitle", "value": "Blazing Fast"}


def test_parse_returns_none_on_unclear() -> None:
    assert parse_edit_instruction("hello there", _plan()) is None


def test_apply_edits_mixed_and_reports_unparsed() -> None:
    plan = _plan()
    out, unparsed = apply_edits(plan, [
        {"op": "retime", "seconds": 40},
        "use a man presenter",
        "asdf jkl",
    ])
    assert out.total_seconds == 40
    assert out.brief.presenter == "man"
    assert unparsed == ["asdf jkl"]


# --- regressions from the adversarial review ---

def test_subtitle_starting_with_to_is_not_eaten() -> None:
    assert parse_edit_instruction("set shot 0 subtitle Top Features", _plan()) == {
        "op": "set_field", "shot": 0, "field": "subtitle", "value": "Top Features"}
    assert parse_edit_instruction("shot 1 subtitle Tokyo Nights", _plan())["value"] == "Tokyo Nights"
    assert parse_edit_instruction("shot 1 subtitle to Hello World", _plan())["value"] == "Hello World"


def test_named_shot_beats_whole_video_retime() -> None:
    assert parse_edit_instruction("shot 2, make it 10 seconds", _plan()) == {
        "op": "set_duration", "shot": 2, "seconds": 10}


def test_no_false_retime_on_comparative_or_narrative() -> None:
    assert parse_edit_instruction("the video was 30 seconds long ago", _plan()) is None
    assert parse_edit_instruction("make it 3 seconds faster", _plan()) is None
    assert parse_edit_instruction("the video is 20 seconds too long", _plan()) is None


def test_no_false_presenter_recast() -> None:
    assert parse_edit_instruction("use a boy band theme in the outro subtitle", _plan()) is None
    assert parse_edit_instruction("use a person's name in the intro", _plan()) is None


def test_apply_edits_flags_noop_structured_op() -> None:
    plan = _plan()
    out, unparsed = apply_edits(plan, [{"op": "remove_shot", "shot": 99}])
    assert out is plan
    assert unparsed == [{"op": "remove_shot", "shot": 99}]
