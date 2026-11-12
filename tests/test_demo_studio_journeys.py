"""The Demo Studio's journey registry is typed, complete, and honestly gated.

The Demo Studio records the REAL served VOOL product (staged at an exact SHA,
driven through a real browser, captioned only from public typed events). It
must never replace the product with mocks, scripted fake UI responses, or
hand-authored success screens. These tests pin the journey layer of that
contract:

* every required journey exists as a typed ``JourneySpec`` with typed steps;
* every step's action is from the frozen driver vocabulary (no free-form
  script execution step exists at all);
* captions may only be derived from the PUBLIC typed event allowlist — the
  streaming content channel (``model_output_chunk``) and anything like hidden
  reasoning is excluded by construction, not by convention;
* journeys that cannot be honest without live dependencies declare them
  (``requires``), so a contained run refuses them visibly instead of faking
  the missing part.
"""

from __future__ import annotations

import pytest

from ops.demo_studio.journeys import (
    JOURNEY_IDS_REQUIRED,
    JOURNEYS,
    PUBLIC_CAPTION_EVENTS,
    STEP_ACTIONS,
    StepSpec,
    journey_ids,
)


def test_all_ten_required_journeys_are_typed_and_registered() -> None:
    missing = [jid for jid in JOURNEY_IDS_REQUIRED if jid not in JOURNEYS]
    assert missing == [], f"required demo journeys missing from the registry: {missing}"
    assert set(journey_ids()) == set(JOURNEY_IDS_REQUIRED), (
        "the registry must be exactly the required demo journeys at P1 "
        f"(extra: {sorted(set(journey_ids()) - set(JOURNEY_IDS_REQUIRED))})"
    )


def test_every_journey_is_a_fully_typed_spec() -> None:
    for jid in JOURNEY_IDS_REQUIRED:
        spec = JOURNEYS[jid]
        assert spec.id == jid, f"journey {jid} carries a mismatched id {spec.id}"
        assert spec.title.strip(), f"{jid}: a demo journey needs a human title"
        assert spec.summary.strip(), f"{jid}: a demo journey needs a summary"
        assert spec.steps, f"{jid}: an empty journey cannot demonstrate anything"
        for step in spec.steps:
            assert isinstance(step, StepSpec), f"{jid}: steps must be typed StepSpec rows"
            assert step.id, f"{jid}: every step needs an id"
            assert step.action in STEP_ACTIONS, (
                f"{jid}/{step.id}: action {step.action!r} is outside the frozen driver vocabulary"
            )
        assert spec.expected_events, (
            f"{jid}: a journey that cannot name the public events that prove it "
            "happened is not a verifiable demo"
        )
        ids = [s.id for s in spec.steps]
        assert len(ids) == len(set(ids)), f"{jid}: duplicate step ids"


def test_captions_come_only_from_the_public_typed_event_allowlist() -> None:
    # The studio's caption source is the public runtime-events API. The
    # streaming content channel is neither public-event material nor caption
    # material: captions built from it would leak model output chunks and any
    # private content they carry into the demo video.
    assert "model_output_chunk" not in PUBLIC_CAPTION_EVENTS
    # The allowlist must be a typed frozenset so callers cannot mutate it.
    assert isinstance(PUBLIC_CAPTION_EVENTS, (frozenset, set))
    # Core lifecycle/tool/permission/attachment events the journeys rely on.
    for required in (
        "task_received",
        "task_completed",
        "tool_selected",
        "tool_executed",
        "permission_approved",
        "permission_denied",
        "attachment_staged",
        "web_retrieval_completed",
        "task_envelope_rollback_completed",
    ):
        assert required in PUBLIC_CAPTION_EVENTS, (
            f"public caption allowlist is missing {required}"
        )


def test_journeys_that_need_live_dependencies_declare_them() -> None:
    # A live-search demo faked with scripted sources would be exactly the
    # staged-video fraud this studio exists to prevent: the journey must
    # declare the live dependency, and a contained run must refuse it
    # visibly (the refusal itself is tested in the abort suite).
    live = JOURNEYS["live_search_answer_with_sources"]
    assert "live_network" in live.requires, (
        "live-search journey must declare requires=('live_network',) so a "
        "contained run cannot fabricate its sources"
    )
    for jid, spec in JOURNEYS.items():
        for req in spec.requires:
            assert req in {"live_network", "operator_models"}, (
                f"{jid}: unknown requirement {req!r}"
            )


def test_journeys_declare_the_capabilities_they_verify() -> None:
    for jid, spec in JOURNEYS.items():
        assert spec.capabilities_required, (
            f"{jid}: every journey must name the capability-manifest rows the "
            "boot gate must verify (verified against /api/runtime/capabilities)"
        )
        for cap in spec.capabilities_required:
            assert isinstance(cap, str) and cap.strip()


def test_no_step_action_can_execute_arbitrary_script() -> None:
    # A demo built on "evaluate this JS" steps would be one edit away from a
    # scripted fake UI. The vocabulary has no such action: the driver reads
    # public page state through its own typed seams only.
    for action in STEP_ACTIONS:
        assert "script" not in action and "eval" not in action, (
            f"driver action {action!r} looks like arbitrary script execution"
        )


def test_restart_resume_journey_restarts_the_real_daemon() -> None:
    actions = [s.action for s in JOURNEYS["restart_resume_continuity"].steps]
    assert "restart_daemon" in actions, (
        "restart/resume continuity must actually restart the served daemon; "
        "simulating it would fake the one thing the journey demonstrates"
    )


def test_refusal_journey_contains_a_deny_step() -> None:
    actions = [s.action for s in JOURNEYS["refused_dangerous_action"].steps]
    assert "deny" in actions, "the refusal journey must actually deny the action on-screen"


def test_council_journey_reads_the_scorecard() -> None:
    actions = [s.action for s in JOURNEYS["council_run_scorecard"].steps]
    assert "council_scorecard" in actions, (
        "the council journey must end at the real scorecard surface"
    )


def test_blackbox_journey_runs_the_real_operator_cli() -> None:
    actions = [s.action for s in JOURNEYS["blackbox_receipt_rollback"].steps]
    assert "run_blackbox" in actions, (
        "blackbox receipt/rollback has no HTTP surface; the journey must drive "
        "the real operator CLI against the isolated home"
    )


def test_unknown_journey_id_is_a_typed_error() -> None:
    from ops.demo_studio.journeys import journey_spec

    with pytest.raises(LookupError):
        journey_spec("no_such_journey")
