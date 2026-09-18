"""R1g2 FAIL-CLOSED AMENDMENT — uncertainty about a secret may never fail open.

THE THREE INDEPENDENTLY VERIFIED DEFECTS (base 83656f0c)
--------------------------------------------------------
1. ``consume_secret_intake("cloud key", owner_local=False)`` returned
   ``consumed_spans=((0, 0),)`` — a ZERO-LENGTH span — with
   ``remainder="cloud key"`` and ``remainder_has_demand=True``: the no-value
   command forms took the MERGED executor path and ran "cloud key" as a demand
   child instead of answering with their original fast response.
2. An exception from ``consume_secret_intake`` in ``agent.py`` was swallowed
   and the RAW input continued — with the intake forced to raise, an exact
   planted secret reached the ``task_received`` event's message and preview
   (and would have continued into models, children, planners and receipts).
3. ``_reject_undeclared_finalization`` caught resolution failures and returned
   the ORIGINAL result — the mechanical fence shipped whatever it could not
   validate.

THE LAW UNDER TEST
------------------
Secret processing fails CLOSED: when the intake cannot prove the text safe,
nothing downstream sees the raw text — no event, model, child, planner,
receipt, telemetry, dialogue or log — and the turn ends in a typed safe
failure/defer response. The mechanical finalization fence fails closed the
same way: a family-resolution failure is a typed internal-routing refusal,
never an unchecked ship. Proven by RED-at-base tests, by leakage probes over
every captured surface, and by three sabotages that restore each fail-open
behavior and turn the named tests RED again.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from core.agent_runtime.secret_intake import consume_secret_intake
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness, _incident_mocks

CLOUD_KEY = "sk-or-v1-ABCDEFGHIJKLMNOPQRST"
IMAGE_KEY = "abcdefghijklmnop:qrstuvwxyz"
PLAIN_SECRET = "hunter2-super-secret-value"

_CLOUD_MIXED = f"cloud key {CLOUD_KEY} openrouter and explain entropy briefly"
_IMAGE_MIXED = f"image key {IMAGE_KEY} and explain entropy briefly"
_BARE_MIXED = f"{CLOUD_KEY} and explain entropy briefly"
_CONFIG_PASTE = f"here is my config api_key: {CLOUD_KEY}\nplease explain what a jwt is"

_ENTROPY_STAND_IN = "ENTROPY-STAND-IN: entropy measures the disorder of a system."

_REMOTE_CONTEXT = dict(_SOURCE_CONTEXT)
_OWNER_CONTEXT = dict(_SOURCE_CONTEXT)
_OWNER_CONTEXT.update({"surface": "desktop", "_owner_local": True})


def _model_stand_in(agent, text: str):
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="r1g3-stand-in", provider_id="r1g3-stand-in",
        provider_name="R1G3 stand-in", model_name="r1g3-stand-in",
        output_text=text, confidence=0.9, trust_score=0.9, used_model=True,
    )
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(agent.memory_router, "resolve", return_value=decision))
    stack.enter_context(
        mock.patch("core.agent_runtime.agent.render_response", return_value=text)
    )
    return stack


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-r1g3-{request.node.name[:44]}")
    try:
        yield h
    finally:
        h.close()


def _served_label(result: dict) -> str:
    return str(result.get("reason") or result.get("route") or "").lower()


# ======================================================================
# 1. DEFECT 1 — the no-value commands keep their original fast response
# ======================================================================


@pytest.mark.parametrize("command,route", [
    ("cloud key", "cloud_key_command"),
    ("image key", "image_key_command"),
], ids=["cloud", "image"])
def test_no_value_command_is_a_pure_query(command, route):
    """The typed intake itself: no zero-length spans, an EMPTY remainder, no
    minted demand — nothing for the merged executor to run."""
    intake = consume_secret_intake(command, owner_local=False)
    assert intake is not None and intake.route == route
    assert intake.remainder == "", (
        f"the no-value command left a remainder ({intake.remainder!r}) — the "
        f"command words would mint as demand"
    )
    assert intake.remainder_has_demand is False
    for start, end in intake.consumed_spans:
        assert start < end, (
            f"zero-length consumed span ({start}, {end}) — the carve carved nothing"
        )


@pytest.mark.parametrize("command,route", [
    ("cloud key", "cloud_key_command"),
    ("image key", "image_key_command"),
], ids=["cloud", "image"])
def test_no_value_command_keeps_its_exact_fast_response(harness, command, route):
    """Remote and owner-local: the EXACT reply the handler itself produces for
    the bare command, under the same route, with ZERO children dispatched."""
    from core.agent_runtime import fast_command_surface as surface

    handler = {
        "cloud_key_command": surface.maybe_handle_cloud_key_command,
        "image_key_command": surface.maybe_handle_image_key_command,
    }[route]
    from apps.vool_agent import VoolAgent

    children: list[str] = []
    real_inner = VoolAgent._run_once_inner

    def _counting(self, user_input, **kwargs):
        if bool((kwargs.get("source_context") or {}).get("planned_subturn")):
            children.append(str(user_input or ""))
        return real_inner(self, user_input, **kwargs)

    for context in (_REMOTE_CONTEXT, _OWNER_CONTEXT):
        expected = handler(command, owner_local=bool(context.get("_owner_local")))
        assert expected, "handler produced no reply for the bare command"
        with mock.patch.object(VoolAgent, "_run_once_inner", _counting):
            with _incident_mocks():
                result = harness.agent.run_once(
                    command, source_context=dict(context),
                    session_id_override=harness.session_id,
                )
        assert str(result.get("response") or "") == expected, (
            f"the no-value command's fast response changed: "
            f"{str(result.get('response'))[:120]!r} != {expected[:120]!r}"
        )
        assert route in _served_label(result), _served_label(result)
    assert not children, f"the no-value command executed demand children: {children}"


# ======================================================================
# 2. DEFECT 2 — an intake failure fails CLOSED over every secret shape
# ======================================================================


def _capture_surfaces(harness):
    """Recorders over child dispatches, model inputs, events and receipts."""
    from apps.vool_agent import VoolAgent

    captured: dict[str, list[str]] = {
        "child_inputs": [], "model_inputs": [], "events": [], "receipts": [],
    }
    real_inner = VoolAgent._run_once_inner
    real_emit = harness.agent._emit_runtime_event
    real_resolve = harness.agent.memory_router.resolve

    def _inner(self, user_input, **kwargs):
        if bool((kwargs.get("source_context") or {}).get("planned_subturn")):
            captured["child_inputs"].append(str(user_input or ""))
        return real_inner(self, user_input, **kwargs)

    def _emit(source_context, **kwargs):
        captured["events"].append(
            str(kwargs.get("message") or "") + " " + str(kwargs.get("request_preview") or "")
        )
        return real_emit(source_context, **kwargs)

    def _resolve(*args, **kwargs):
        captured["model_inputs"].append(
            " ".join(str(a) for a in args)
            + " "
            + " ".join(f"{k}={v}" for k, v in kwargs.items())
        )
        return real_resolve(*args, **kwargs)

    captured["_patches"] = [
        mock.patch.object(VoolAgent, "_run_once_inner", _inner),
        mock.patch.object(harness.agent, "_emit_runtime_event", _emit),
        mock.patch.object(harness.agent.memory_router, "resolve", side_effect=_resolve),
        # Bound: the FAIL-OPEN behavior under test lets the raw text continue
        # into the conductor/planner, whose local-model calls run for minutes.
        # The stub answers instantly so the leak assertions stay the point.
        mock.patch(
            "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
            return_value=lambda _system, _prompt: "",
        ),
    ]
    return captured


def _dialogue_text(session_id: str) -> str:
    from storage.dialogue_memory import recent_dialogue_turns

    rows = recent_dialogue_turns(session_id, limit=24)
    return " ".join(str(row.get("content") or "") for row in rows)


@pytest.mark.parametrize("text,secret", [
    (_CLOUD_MIXED, CLOUD_KEY),
    (_IMAGE_MIXED, IMAGE_KEY),
    (_BARE_MIXED, CLOUD_KEY),
    (_CONFIG_PASTE, CLOUD_KEY),
], ids=["cloud", "image", "bare", "config-paste"])
def test_intake_failure_fails_closed_on_every_secret_shape(harness, text, secret):
    """With the intake FORCED to raise, the raw text may reach NO surface —
    not the task_received event, not a model, not a child, not the dialogue
    store — and the turn ends in a typed safe failure/defer response."""
    captured = _capture_surfaces(harness)
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
        with contextlib.ExitStack() as stack:
            for patch in captured.pop("_patches"):
                stack.enter_context(patch)
            stack.enter_context(
                mock.patch(
                    "core.agent_runtime.secret_intake.consume_secret_intake",
                    side_effect=RuntimeError("intake exploded"),
                )
            )
            result = harness.agent.run_once(
                text, source_context=dict(_REMOTE_CONTEXT),
                session_id_override=harness.session_id,
            )
    answer = str(result.get("response") or "")
    assert "secret_intake_failed" in _served_label(result), (
        f"the turn did not end in the typed safe failure ({_served_label(result)!r}): "
        f"{answer[:160]!r}"
    )
    assert secret not in answer, f"the response itself echoed the secret: {answer[:160]!r}"
    for surface_name, samples in captured.items():
        for sample in samples:
            assert secret not in sample, (
                f"raw secret bytes surfaced in {surface_name} under an intake "
                f"failure: {sample[:160]!r}"
            )
    assert secret not in _dialogue_text(harness.session_id), (
        "raw secret bytes surfaced in the dialogue store under an intake failure"
    )


def test_intake_failure_never_runs_children_or_models(harness):
    """The fail-closed response is terminal: zero child dispatches, zero model
    calls — nothing executed that could carry the raw text onward."""
    captured = _capture_surfaces(harness)
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
        with contextlib.ExitStack() as stack:
            for patch in captured.pop("_patches"):
                stack.enter_context(patch)
            stack.enter_context(
                mock.patch(
                    "core.agent_runtime.secret_intake.consume_secret_intake",
                    side_effect=RuntimeError("intake exploded"),
                )
            )
            harness.agent.run_once(
                _CLOUD_MIXED, source_context=dict(_REMOTE_CONTEXT),
                session_id_override=harness.session_id,
            )
    assert not captured["child_inputs"], captured["child_inputs"]
    assert not captured["model_inputs"], captured["model_inputs"]


# ======================================================================
# 3. DEFECT 3 — the mechanical fence fails closed
# ======================================================================


def _free_models_turn(harness) -> dict:
    with _incident_mocks():
        return harness.agent.run_once(
            "what free models are on offer",
            source_context=dict(_REMOTE_CONTEXT),
            session_id_override=harness.session_id,
        )


def test_family_resolution_failure_fails_closed(harness):
    """finalization_family raising must produce the typed internal-routing
    refusal — never the unchecked original result."""
    from core import lane_registry

    with mock.patch.object(
        lane_registry, "finalization_family", side_effect=RuntimeError("registry exploded")
    ):
        result = _free_models_turn(harness)
    label = _served_label(result)
    assert "finalization_check_failed" in label, (
        f"a resolution failure shipped the unchecked result ({label!r}): "
        f"{str(result.get('response'))[:120]!r}"
    )
    assert "unregistered route" in str(result.get("response") or "").lower()


def test_catalog_lookup_failure_fails_closed(harness):
    """find_spec raising inside the fence must fail closed the same way."""
    from core import lane_registry

    with mock.patch.object(
        lane_registry, "find_spec", side_effect=RuntimeError("catalog exploded")
    ):
        result = _free_models_turn(harness)
    assert "finalization_check_failed" in _served_label(result), _served_label(result)


def test_the_fence_still_passes_declared_finalizations(harness):
    """Control: the production catalog in force, the intent finalizes."""
    result = _free_models_turn(harness)
    assert "free_models" in _served_label(result), _served_label(result)
    assert "finalization_check_failed" not in _served_label(result)


# ======================================================================
# 4. SABOTAGES — restoring each fail-open behavior turns tests RED
# ======================================================================


def test_sabotage_zero_length_command_span_is_caught():
    """A degenerate matcher (zero-length match geometry) must fail closed —
    no span may carve nothing and hand the RAW text on as a remainder. Green
    here means the guard catches the geometry defect class; the file-level
    sabotage (guard removed) turns this RED."""
    from core.agent_runtime import fast_command_surface
    from core.agent_runtime import secret_intake as si

    class _Degenerate:
        def start(self, _n=0):
            return 0

        def end(self, _n=0):
            return 0

        def group(self, _n=0):
            return ""

    with mock.patch.object(
        fast_command_surface, "cloud_key_command_match", return_value=_Degenerate()
    ):
        intake = si.consume_secret_intake(_CLOUD_MIXED, owner_local=False)
    assert intake is not None
    assert CLOUD_KEY not in intake.remainder, (
        f"a degenerate span handed the raw text (with the secret) on as the "
        f"remainder: {intake.remainder!r}"
    )
    assert intake.remainder_has_demand is False, (
        "a degenerate span must not route the raw text into the merged executor"
    )


def test_sabotage_fail_open_catalog_exception_turns_tests_red(harness):
    """Restoring the OLD fail-open fence (exceptions return the original
    result) must make the fail-closed fence test fail."""
    from core import lane_registry
    from core.agent_runtime import agent as agent_module

    with mock.patch.object(
        lane_registry, "finalization_family", side_effect=RuntimeError("registry exploded")
    ), mock.patch.object(agent_module, "_reject_undeclared_finalization", lambda r: r):
        result = _free_models_turn(harness)
    assert "free_models" in _served_label(result) and (
        "finalization_check_failed" not in _served_label(result)
    ), "the fail-open fence sabotage did not ship the unchecked result"
