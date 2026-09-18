"""R1g AMENDMENT — secret safety must not drop other demands.

THE INDEPENDENTLY REPRODUCED DEFECT (base 610076c0)
---------------------------------------------------
All three inputs finalize through a secret route and silently discard the
ordinary demand beside it:

    "cloud key sk-or-v1-… openrouter and explain entropy briefly"
      -> deterministic:cloud_key_command — key reply only, entropy NEVER RUNS
    "image key abc…:xyz… and explain entropy briefly"
      -> deterministic:image_key_command  — same swallow
    "sk-or-v1-… and explain entropy briefly"
      -> deterministic:bare_secret_intercept — same swallow

The R1g comment exempting the secret-consuming intents from the demand gate was
unsafe: the exemption is right about WHERE the secret may go (never a model)
and wrong about WHAT the turn owes (every non-secret demand still discharges).

THE CONTRACT UNDER TEST
-----------------------
A typed secret intake runs BEFORE any lane: it consumes the credential
(disposition + safe reply + exact consumed span), sanitizes the remainder, and
— when the remainder carries demand — executes it as owned outcomes of the SAME
canonical turn, merging the credential outcome with the remainder's truthfully.
Secret bytes never reach child input, model input, planner task, dialogue,
runtime event, receipt, telemetry or logs. A secret-bearing text with NO
remainder demand keeps today's fast response byte-for-byte.

The matrix is deterministic: model answers come from the R1e typed stand-in,
expected atoms are asserted by string comparison, no socket is opened. The
credential store side effects are mocked at the seal boundary so no disk state
is written and the intended-store positive control is observable.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness, _incident_mocks

CLOUD_KEY = "sk-or-v1-ABCDEFGHIJKLMNOPQRST"
IMAGE_KEY = "abcdefghijklmnop:qrstuvwxyz"
PLAIN_SK = "sk-ABCDEFGHIJKLMNOPQRSTUV1234"  # ambiguous provider prefix (no -or-/-ant-)

_ENTROPY_STAND_IN = "ENTROPY-STAND-IN: entropy measures the disorder of a system."
_HAIKU_STAND_IN = "HAIKU-STAND-IN: rain on the copper roof, quietly."
_JWT_STAND_IN = "JWT-STAND-IN: a signed three-part token."

_CLOUD_MIXED = f"cloud key {CLOUD_KEY} openrouter and explain entropy briefly"
_IMAGE_MIXED = f"image key {IMAGE_KEY} and explain entropy briefly"
_BARE_MIXED = f"{CLOUD_KEY} and explain entropy briefly"
_BARE_MIXED_TRAILING = f"explain entropy briefly and {CLOUD_KEY}"
_TWO_DEMANDS = (
    f"cloud key {CLOUD_KEY} openrouter, explain entropy briefly "
    "and write a haiku about rain"
)
_MALFORMED = "cloud key short and explain entropy briefly"
_AMBIGUOUS = f"{PLAIN_SK} and explain entropy briefly"
_CONFIG_PASTE = (
    "here is my config api_key: " + CLOUD_KEY + "\nplease explain what a jwt is"
)

_REMOTE_CONTEXT = dict(_SOURCE_CONTEXT)
_OWNER_CONTEXT = dict(_SOURCE_CONTEXT)
_OWNER_CONTEXT.update({"surface": "desktop", "_owner_local": True})

_SEALED_REPLY = "Sealed your test cloud key (…QRST) in the encrypted store."


class _SealSpy:
    """Mock the seal boundary: no disk writes; records what the credential
    path received (the positive control — the intended store MAY see the key)."""

    def __init__(self) -> None:
        self.received: list[str] = []

    def enter(self, stack: contextlib.ExitStack) -> None:
        from core import media_tools
        from core.agent_runtime import fast_command_surface

        def _seal_cloud(secret, provider_id="openrouter"):
            self.received.append(str(secret))
            return _SEALED_REPLY

        def _configure_image(api_key, provider="fal", model=""):
            self.received.append(str(api_key))
            return True, str(api_key)[-4:]

        stack.enter_context(
            mock.patch.object(fast_command_surface, "_seal_cloud_key", side_effect=_seal_cloud)
        )
        stack.enter_context(
            mock.patch.object(media_tools, "configure_image_service", side_effect=_configure_image)
        )
        stack.enter_context(mock.patch.object(media_tools, "has_image_service", return_value=True))
        stack.enter_context(mock.patch.object(media_tools, "detect_fal_key", return_value=True))


def _model_stand_in(agent, text: str, also: dict[str, str] | None = None):
    """The R1e typed model stand-in through the real model lane; no socket.

    `also` maps a keyword to an alternative stand-in text, so a turn with TWO
    general children gets a per-request answer (the stand-in inspects the
    request it is handed — still no LLM oracle, the mapping is fixed)."""
    from core.memory_first_router import ModelExecutionDecision

    def _answer_for(request_blob: str) -> str:
        for keyword, alternate in (also or {}).items():
            if keyword in request_blob:
                return alternate
        return text

    def _resolve(*args, **kwargs):
        # Match on the TASK object the router was handed — the request's own
        # words, never the sibling history, so two general children each get
        # their own stand-in deterministically.
        blob = str(kwargs.get("task") or "")
        return ModelExecutionDecision(
            source="model", task_hash="r1g2-stand-in", provider_id="r1g2-stand-in",
            provider_name="R1G2 stand-in", model_name="r1g2-stand-in",
            output_text=_answer_for(blob), confidence=0.9, trust_score=0.9,
            used_model=True,
        )

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(agent.memory_router, "resolve", side_effect=_resolve))
    stack.enter_context(
        mock.patch(
            "core.agent_runtime.agent.render_response",
            side_effect=lambda *a, **_k: _answer_for(" ".join(map(str, a))),
        )
    )
    return stack


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-r1g2-{request.node.name[:44]}")
    try:
        yield h
    finally:
        h.close()


def _turn(harness, text, *, context, extra_stacks=(), stand_in_alternates=None):
    with _incident_mocks(), _model_stand_in(
        harness.agent, _ENTROPY_STAND_IN, also=stand_in_alternates
    ):
        with contextlib.ExitStack() as stack:
            for enter in extra_stacks:
                enter(stack)
            result = harness.agent.run_once(
                text, source_context=dict(context), session_id_override=harness.session_id
            )
    return result


def _served_label(result: dict) -> str:
    reason = str(result.get("reason") or "")
    route = str(result.get("route") or "")
    return (reason or route).lower()


# ======================================================================
# 1. THE REPRODUCED DEFECT — the ordinary demand must survive every route
# ======================================================================


def test_cloud_key_plus_demand_discharges_both(harness):
    result = _turn(harness, _CLOUD_MIXED, context=_REMOTE_CONTEXT)
    answer = str(result.get("response") or "")
    assert "cloud key" in answer.lower(), (
        f"the credential half of the turn vanished: {answer[:160]!r}"
    )
    assert _ENTROPY_STAND_IN in answer, (
        f"the entropy demand was dropped by the secret route "
        f"({_served_label(result)!r}): {answer[:160]!r}"
    )


def test_image_key_plus_demand_discharges_both(harness):
    result = _turn(harness, _IMAGE_MIXED, context=_REMOTE_CONTEXT)
    answer = str(result.get("response") or "")
    assert "image key" in answer.lower(), answer[:160]
    assert _ENTROPY_STAND_IN in answer, (
        f"the entropy demand was dropped by the secret route "
        f"({_served_label(result)!r}): {answer[:160]!r}"
    )


def test_bare_key_plus_demand_discharges_both(harness):
    result = _turn(harness, _BARE_MIXED, context=_REMOTE_CONTEXT)
    answer = str(result.get("response") or "")
    assert "cloud key" in answer.lower() or "own local session" in answer.lower(), answer[:160]
    assert _ENTROPY_STAND_IN in answer, (
        f"the entropy demand was dropped by the secret route "
        f"({_served_label(result)!r}): {answer[:160]!r}"
    )


def test_bare_key_after_the_demand_also_discharges_both(harness):
    """The other syntactic order for a position-free bare key."""
    result = _turn(harness, _BARE_MIXED_TRAILING, context=_REMOTE_CONTEXT)
    answer = str(result.get("response") or "")
    assert _ENTROPY_STAND_IN in answer, (
        f"the entropy demand was dropped by the secret route "
        f"({_served_label(result)!r}): {answer[:160]!r}"
    )


def test_secret_plus_two_unrelated_demands_discharges_all_three(harness):
    result = _turn(
        harness,
        _TWO_DEMANDS,
        context=_REMOTE_CONTEXT,
        stand_in_alternates={"haiku": _HAIKU_STAND_IN},
    )
    answer = str(result.get("response") or "")
    assert "cloud key" in answer.lower(), answer[:160]
    assert _ENTROPY_STAND_IN in answer, f"entropy demand dropped: {answer[:200]!r}"
    assert _HAIKU_STAND_IN in answer, f"haiku demand dropped: {answer[:200]!r}"


# ======================================================================
# 2. owner-local success and non-owner refusal BOTH preserve the remainder
# ======================================================================


def test_owner_local_sealed_and_remainder_discharges(harness):
    spy = _SealSpy()
    result = _turn(harness, _CLOUD_MIXED, context=_OWNER_CONTEXT, extra_stacks=[spy.enter])
    answer = str(result.get("response") or "")
    assert _SEALED_REPLY in answer, f"the credential outcome was lost: {answer[:200]!r}"
    assert _ENTROPY_STAND_IN in answer, (
        f"the remainder was dropped on owner-local success: {answer[:200]!r}"
    )
    assert spy.received == [CLOUD_KEY], (
        f"the intended credential path must receive the key exactly once: {spy.received!r}"
    )


def test_remote_refusal_still_preserves_the_remainder(harness):
    result = _turn(harness, _CLOUD_MIXED, context=_REMOTE_CONTEXT)
    answer = str(result.get("response") or "")
    assert "own local session" in answer.lower(), answer[:160]
    assert _ENTROPY_STAND_IN in answer


def test_malformed_credential_with_valid_remainder_still_discharges(harness):
    """A too-short key is rejected by the credential handler; the remainder is
    still the user's demand and must still run."""
    result = _turn(harness, _MALFORMED, context=_OWNER_CONTEXT)
    answer = str(result.get("response") or "")
    assert "not a complete" in answer.lower() or "does not look like" in answer.lower(), (
        f"the credential rejection reply was lost: {answer[:200]!r}"
    )
    assert _ENTROPY_STAND_IN in answer, (
        f"a malformed key dropped the valid remainder: {answer[:200]!r}"
    )


def test_ambiguous_bare_key_with_remainder_asks_and_discharges(harness):
    """An ambiguous plain `sk-` asks for the provider instead of storing; the
    remainder still runs."""
    result = _turn(harness, _AMBIGUOUS, context=_OWNER_CONTEXT)
    answer = str(result.get("response") or "")
    assert "ambiguous" in answer.lower(), answer[:200]
    assert _ENTROPY_STAND_IN in answer, (
        f"the ambiguous-key ask dropped the remainder: {answer[:200]!r}"
    )


# ======================================================================
# 3. pasted/config-like text: sanitize before the model, keep the request
# ======================================================================


def test_config_paste_with_request_answers_without_the_secret(harness):
    """A long config-like paste carries a labelled secret plus a request. No
    intake route claims it (the bare-key route is bounded to short texts); the
    cascade must answer the request on the REDACTED text — the model input and
    the reply never carry the secret bytes."""
    calls: list[str] = []
    real_resolve = harness.agent.memory_router.resolve

    def _recording_resolve(*args, **kwargs):
        calls.append(" ".join(str(a) for a in args) + " " + " ".join(f"{k}={v}" for k, v in kwargs.items()))
        return real_resolve(*args, **kwargs)

    with _incident_mocks(), _model_stand_in(harness.agent, _JWT_STAND_IN):
        with mock.patch.object(
            harness.agent.memory_router, "resolve", side_effect=_recording_resolve
        ):
            harness.agent.run_once(
                _CONFIG_PASTE, source_context=dict(_REMOTE_CONTEXT),
                session_id_override=harness.session_id,
            )
    assert calls, "the request never reached the model lane"
    assert not any(CLOUD_KEY in call for call in calls), (
        f"the exact secret bytes reached the model lane input: {[c[:120] for c in calls]}"
    )


# ======================================================================
# 4. LEAKAGE — exact secret bytes absent from every captured surface
# ======================================================================


def _capture_surfaces(harness):
    """Recorders over every surface the turn can write: child dispatch texts,
    model inputs, dialogue rows, runtime events, receipts, observations."""
    from apps.vool_agent import VoolAgent

    captured: dict[str, list[str]] = {
        "child_inputs": [], "model_inputs": [], "events": [], "receipts": [],
        "observations": [],
    }
    real_inner = VoolAgent._run_once_inner
    real_emit = harness.agent._emit_runtime_event
    real_resolve = harness.agent.memory_router.resolve

    def _inner(self, user_input, **kwargs):
        if bool((kwargs.get("source_context") or {}).get("planned_subturn")):
            captured["child_inputs"].append(str(user_input or ""))
        return real_inner(self, user_input, **kwargs)

    def _emit(source_context, **kwargs):
        captured["events"].append(str(kwargs.get("message") or "") + " " + str(kwargs.get("request_preview") or ""))
        return real_emit(source_context, **kwargs)

    def _resolve(*args, **kwargs):
        captured["model_inputs"].append(
            " ".join(str(a) for a in args) + " " + " ".join(f"{k}={v}" for k, v in kwargs.items())
        )
        return real_resolve(*args, **kwargs)

    captured["_patches"] = [
        mock.patch.object(VoolAgent, "_run_once_inner", _inner),
        mock.patch.object(harness.agent, "_emit_runtime_event", _emit),
        mock.patch.object(harness.agent.memory_router, "resolve", side_effect=_resolve),
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
], ids=["cloud", "image", "bare"])
def test_secret_bytes_never_reach_any_captured_surface(harness, text, secret):
    spy = _SealSpy()
    captured = _capture_surfaces(harness)
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
        with contextlib.ExitStack() as stack:
            for patch in captured.pop("_patches"):
                stack.enter_context(patch)
            spy.enter(stack)
            harness.agent.run_once(
                text, source_context=dict(_OWNER_CONTEXT),
                session_id_override=harness.session_id,
            )
    dialogue = _dialogue_text(harness.session_id)
    for surface, samples in captured.items():
        for sample in samples:
            assert secret not in sample, (
                f"exact secret bytes surfaced in {surface}: {sample[:160]!r}"
            )
    assert secret not in dialogue, (
        f"exact secret bytes surfaced in the dialogue store: {dialogue[:200]!r}"
    )
    # The intended credential path is the ONE surface that must have them.
    assert spy.received == [secret], (
        f"the credential path must receive the key exactly once: {spy.received!r}"
    )


# ======================================================================
# 5. ONE canonical turn, no duplicate execution
# ======================================================================


def test_merged_secret_turn_keeps_one_identity_and_no_duplicate_dispatch(harness):
    from collections import Counter

    from core.turn_contract import TURN_REQUEST_KEY

    spy = _SealSpy()
    captured = _capture_surfaces(harness)
    with _incident_mocks(), _model_stand_in(
        harness.agent, _ENTROPY_STAND_IN, also={"haiku": _HAIKU_STAND_IN}
    ):
        with contextlib.ExitStack() as stack:
            for patch in captured.pop("_patches"):
                stack.enter_context(patch)
            spy.enter(stack)
            context = dict(_OWNER_CONTEXT)
            result = harness.agent.run_once(
                _TWO_DEMANDS, source_context=context, session_id_override=harness.session_id
            )
    answer = str(result.get("response") or "")
    assert _SEALED_REPLY in answer and _ENTROPY_STAND_IN in answer and _HAIKU_STAND_IN in answer

    assert isinstance(context.get(TURN_REQUEST_KEY), object) and str(
        context[TURN_REQUEST_KEY].turn_id
    ), "no canonical TurnRequest on the merged turn"

    from storage.dialogue_memory import recent_dialogue_turns

    user_rows = [
        row for row in recent_dialogue_turns(harness.session_id, limit=16)
        if str(row.get("speaker_role") or "user") == "user"
    ]
    ids = [str(row.get("turn_id") or "") for row in user_rows]
    assert ids == [context[TURN_REQUEST_KEY].turn_id], (
        f"one external secret turn wrote {len(ids)} user dialogue rows ({ids})"
    )

    counts = Counter(captured["child_inputs"])
    duplicated = [t for t, n in counts.items() if n > 1]
    assert not duplicated, f"a demand unit was dispatched more than once: {duplicated}"
    assert captured["child_inputs"], "the remainder never executed as owned outcomes"
    assert spy.received == [CLOUD_KEY], (
        f"the credential was consumed more than once: {spy.received!r}"
    )


# ======================================================================
# 6. CONTROL — a secret with no remainder demand keeps today's fast path
# ======================================================================


def test_secret_with_no_remainder_keeps_the_fast_response(harness):
    """`cloud key <key>` alone (both dispositions) still finalizes exactly as
    before: same route, no children, no demand machinery."""
    result = _turn(harness, f"cloud key {CLOUD_KEY}", context=_REMOTE_CONTEXT)
    label = _served_label(result)
    assert "cloud_key" in label, f"the pure secret turn changed route: {label!r}"
    assert "own local session" in str(result.get("response") or "").lower()


def test_pure_secret_turn_executes_no_children(harness):
    captured = _capture_surfaces(harness)
    with _incident_mocks():
        with contextlib.ExitStack() as stack:
            for patch in captured.pop("_patches"):
                stack.enter_context(patch)
            harness.agent.run_once(
                f"cloud key {CLOUD_KEY}", source_context=dict(_REMOTE_CONTEXT),
                session_id_override=harness.session_id,
            )
    assert not captured["child_inputs"], (
        f"a no-remainder secret turn ran demand children: {captured['child_inputs']}"
    )


# ======================================================================
# 7. REQUIREMENT 8 — undeclared finalization is mechanically rejected
# ======================================================================


def test_every_registered_route_family_is_declared_in_the_catalog():
    """The mechanical registry: every route→family mapping must point at a
    lane the ACTIVE (production) catalog declares."""
    from core import lane_registry

    declared = {spec.lane_id for spec in lane_registry.LANE_CATALOG}
    undeclared = sorted(
        {family for family in lane_registry.ROUTE_FAMILIES.values() if family not in declared}
    )
    assert not undeclared, (
        f"routes finalize under families absent from LANE_CATALOG: {undeclared}"
    )


def test_an_undeclared_family_finalization_is_refused_at_the_seam(harness):
    """The runtime law, not a test-time tuple: with the frontdoor family
    removed from the ACTIVE catalog, a deterministic finalization under one of
    its routes is mechanically refused at the common seam — it may not ship."""
    from core import lane_registry

    sabotaged = tuple(
        s for s in lane_registry.LANE_CATALOG if s.lane_id != "turn_frontdoor_deterministic"
    )
    with lane_registry.scoped_catalog(sabotaged):
        with _incident_mocks():
            shipped = harness.agent.run_once(
                "what free models are on offer",
                source_context=dict(_REMOTE_CONTEXT),
                session_id_override=harness.session_id,
            )
    label = _served_label(shipped)
    assert "undeclared" in label or "undeclared" in str(shipped.get("response") or "").lower(), (
        f"a deterministic route finalized under a family removed from the catalog "
        f"and shipped: {label!r} / {str(shipped.get('response'))[:120]!r}"
    )


def test_a_declared_family_finalization_still_ships(harness):
    """Control: with the production catalog in force the same turn finalizes
    through its intent (whatever the catalog fetch's own availability says) and
    is NOT refused by the declaration seam."""
    with _incident_mocks():
        result = harness.agent.run_once(
            "what free models are on offer",
            source_context=dict(_REMOTE_CONTEXT),
            session_id_override=harness.session_id,
        )
    assert "free_models" in _served_label(result), _served_label(result)
    assert "undeclared" not in _served_label(result)
