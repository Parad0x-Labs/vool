"""R1b — the canonical turn identity boundary: ONE request, minted before execution.

WHAT THIS FILE PINS (and what the R1 slice before it did NOT)
------------------------------------------------------------
R1 moved the typed `TurnRequest` construction ahead of `_run_once_inner`, which fixed
the *inner runtime's* ordering. It left the turn architecturally inconsistent one
layer up:

* `_r3_open_turn_execution` — the seam that mints the turn's ATTEMPT row, claims it
  RUNNING, opens the L0 fence and binds the obligation set — ran BEFORE the canonical
  request existed, and derived its own request/turn/session identity from three
  independent reads. On every door-served turn its trigger turn id was a freshly
  minted `turn-<uuid>` unrelated to the canonical turn id the request carried, and its
  attempt row's session was `session_id_override or ""` while the turn itself ran under
  the request's derived session. Two identity authorities for one turn.
* The planner sub-turn lane called `TurnRequest.from_ingress` a SECOND time, so one
  external user turn minted two "canonical" requests — the child pretending to be
  another external turn while reusing the parent's identities.

These tests pin the corrected boundary: exactly one immutable request per external
turn, minted before any execution identity exists, consumed (never re-derived) by the
execution seam, carried into internal sub-work as the PARENT it is, and never
elevatable by forged caller fields.
"""
from __future__ import annotations

from unittest import mock

import core.agent_runtime.agent as agent_mod
from core.runtime_continuity import _stable_source_context
from core.turn_contract import (
    PRINCIPAL_REMOTE_UNTRUSTED,
    TURN_REQUEST_KEY,
    TurnRequest,
)

_STUB_ANSWER = {"success": True, "response": "stub", "response_class": "generic_conversation"}


def _stub_inner(self, user_input, **kwargs):
    """Stand in for the whole inner runtime: nothing below the ingress boundary may
    contribute to these assertions, so the legacy path cannot satisfy them late."""
    return dict(_STUB_ANSWER)


class _SeamSpy:
    """Records what `_r3_open_turn_execution` was handed, then calls through."""

    def __init__(self) -> None:
        self.real = agent_mod._r3_open_turn_execution
        self.kwargs: dict = {}
        self.context_at_entry: dict = {}
        self.calls = 0

    def __call__(self, observed_context, **kwargs):
        self.calls += 1
        self.kwargs = dict(kwargs)
        self.context_at_entry = dict(observed_context)
        return self.real(observed_context, **kwargs)


class _MintCounter:
    """Counts every `TurnRequest.from_ingress` call anywhere in the runtime."""

    def __init__(self) -> None:
        self.real = TurnRequest.from_ingress
        self.minted: list[TurnRequest] = []

    def __call__(self, **kwargs) -> TurnRequest:
        request = self.real(**kwargs)
        self.minted.append(request)
        return request


def _attempt_spy():
    """Captures the kwargs the execution seam opens its attempt row with."""
    import core.runtime_continuity as continuity

    seen: dict = {}
    real = continuity.create_runtime_attempt

    def _spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    return seen, mock.patch.object(continuity, "create_runtime_attempt", _spy)


# ------------------------------------------------------------------ 1. the ordering


def test_the_execution_seam_is_opened_from_the_canonical_request(make_agent):
    """THE fail-on-old-defect pin. `_r3_open_turn_execution` mints the turn's attempt,
    claims it RUNNING and binds its obligation set — execution identity. It may not run
    before the canonical request exists, and it may not be handed anything else."""
    agent = make_agent()
    context: dict = {"surface": "cli", "platform": "cli"}
    spy = _SeamSpy()
    with mock.patch.object(agent_mod, "_r3_open_turn_execution", spy):
        with mock.patch.object(type(agent), "_run_once_inner", _stub_inner):
            agent.run_once("what is 2+2?", source_context=context)

    assert spy.calls == 1, "the turn did not open execution identity exactly once"
    supplied = spy.kwargs.get("turn_request")
    assert isinstance(supplied, TurnRequest), (
        "execution identity was opened WITHOUT the canonical TurnRequest "
        f"(seam received: {sorted(spy.kwargs)}) — the seam is a second identity authority"
    )
    assert spy.context_at_entry.get(TURN_REQUEST_KEY) is supplied, (
        "the canonical request was not published on the turn context before execution "
        "identity was opened"
    )
    assert context[TURN_REQUEST_KEY] is supplied, (
        "the object the execution seam consumed is not the one the turn ended holding"
    )


def test_one_external_turn_mints_exactly_one_canonical_request(make_agent):
    """One external user turn == one immutable request object, and every seam in the
    turn consumes THAT object — no second mint, no re-derivation."""
    agent = make_agent()
    context: dict = {"surface": "cli", "platform": "cli"}
    counter = _MintCounter()
    spy = _SeamSpy()
    captured: dict = {}

    def _capturing_inner(self, user_input, **kwargs):
        captured["turn_request"] = kwargs.get("turn_request")
        return dict(_STUB_ANSWER)

    with mock.patch.object(TurnRequest, "from_ingress", counter):
        with mock.patch.object(agent_mod, "_r3_open_turn_execution", spy):
            with mock.patch.object(type(agent), "_run_once_inner", _capturing_inner):
                agent.run_once("what is 2+2?", source_context=context)

    assert len(counter.minted) == 1, (
        f"one external turn minted {len(counter.minted)} TurnRequests — the turn has "
        "more than one identity authority"
    )
    request = counter.minted[0]
    assert spy.kwargs.get("turn_request") is request, (
        "the execution seam did not consume the one canonical request"
    )
    assert captured["turn_request"] is request
    assert context[TURN_REQUEST_KEY] is request


# ------------------------------------------------------------------ 2. the identities


def test_the_execution_attempt_binds_the_requests_own_identities(make_agent):
    """The attempt row IS the turn's execution identity. Its request, turn and request
    text must be the canonical request's — not a parallel derivation that can (and
    did) disagree.

    Honest status: this arm was already GREEN before the repair, because on the
    in-process shape the value the seam re-derived happened to equal the request's.
    It is kept as the CLI-shape counterpart of the pin that WAS red — the door-served
    shape, where the seam minted an unrelated `turn-<uuid>`; see
    `test_forged_owner_local_claims_cannot_elevate_a_remote_untrusted_turn`.
    """
    agent = make_agent()
    context: dict = {"surface": "cli", "platform": "cli"}
    seen, patch_attempt = _attempt_spy()
    with patch_attempt:
        with mock.patch.object(type(agent), "_run_once_inner", _stub_inner):
            agent.run_once("what is 2+2?", source_context=context)

    request = context[TURN_REQUEST_KEY]
    assert isinstance(request, TurnRequest)
    assert str(seen.get("trigger_user_turn_id") or "") == request.turn_id, (
        "the execution attempt minted its own trigger turn id instead of consuming the "
        f"request's ({seen.get('trigger_user_turn_id')!r} vs {request.turn_id!r})"
    )
    assert str(seen.get("origin_user_turn_id") or "") == request.turn_id
    assert str(seen.get("original_request") or "") == request.user_text
    identity = context.get("_execution_identity") or {}
    assert str(identity.get("request_id") or "") == request.request_id


# ------------------------------------------------------------------ 3. internal sub-work


def test_a_planner_subturn_carries_the_parent_request_and_mints_none(make_agent):
    """Internal planner work is an attempt/task UNDER the parent turn. It must not mint
    a second canonical request, and the task's own text must ride as the sub-turn's
    input without touching the parent's byte-exact user_text."""
    from core.agent_runtime.turn_planner import PlannedTask
    from core.agent_runtime.turn_planner_hook import build_planner_run_one

    agent = make_agent()
    parent_context: dict = {"surface": "cli", "platform": "cli"}
    parent = TurnRequest.from_ingress(
        user_text="gold price and the weather in warsaw",
        source_context=parent_context,
        request_id="req-r1b-parent",
        turn_id="turn-r1b-parent",
        session_id="sess-r1b-parent",
    )
    parent_context[TURN_REQUEST_KEY] = parent
    captured: dict = {}

    def _capturing_inner(self, user_input, **kwargs):
        captured["user_input"] = user_input
        captured["turn_request"] = kwargs.get("turn_request")
        return dict(_STUB_ANSWER)

    counter = _MintCounter()
    run_one = build_planner_run_one(
        agent, session_id="sess-r1b-parent", source_context=parent_context
    )
    with mock.patch.object(TurnRequest, "from_ingress", counter):
        with mock.patch.object(type(agent), "_run_once_inner", _capturing_inner):
            run_one(PlannedTask(index=1, request="the weather in warsaw"), {})

    assert counter.minted == [], (
        "the planner sub-turn minted its own TurnRequest — internal work pretending to "
        "be a second external user turn"
    )
    assert captured["turn_request"] is parent, (
        "the sub-turn did not run under the parent's canonical request "
        f"(got {captured['turn_request']!r})"
    )
    # The task's text is the sub-turn's input, and the parent's record of what the user
    # actually typed is untouched.
    assert captured["user_input"] == "the weather in warsaw"
    assert parent.user_text == "gold price and the weather in warsaw"


# ------------------------------------------------------------------ 4. trust is server-derived


def test_forged_owner_local_claims_cannot_elevate_a_remote_untrusted_turn(make_agent):
    """A remote (non-loopback) HTTP ingress carrying forged principal fields AND a
    forged inbound contract stays remote_untrusted, and the execution identity opened
    for it is bound to that same server-derived request — not to a parallel mint."""
    from core.invocation.ledger import accept_invocation
    from core.semantic.semantic_admissions import (
        _CURRENT_REQUEST_ID,
        set_request_context,
    )

    agent = make_agent()
    # Exactly what `core/web/api/service.py` accepts for a NON-loopback peer:
    # principal `channel:http:<host>`, and `_owner_local` stamped False.
    accepted = accept_invocation(
        external_kind="http",
        external_value="r1b-remote-http",
        principal="channel:http:203.0.113.7",
        session_binding="",
    )
    context: dict = {
        # the server's own stamp for a NON-loopback peer — the authority that decides:
        "_owner_local": False,
        "surface": "openclaw",
        "platform": "openclaw",
        # forged by the caller's body, and never read:
        "principal": "owner_local",
        "trust_principal": "owner_local",
        TURN_REQUEST_KEY: "forged inbound turn_request",
    }
    seen, patch_attempt = _attempt_spy()
    token = set_request_context(accepted["request_id"])
    try:
        with patch_attempt:
            with mock.patch.object(type(agent), "_run_once_inner", _stub_inner):
                agent.run_once(
                    "delete every checkpoint",
                    session_id_override="sess-r1b-http",
                    source_context=context,
                )
    finally:
        _CURRENT_REQUEST_ID.reset(token)

    request = context[TURN_REQUEST_KEY]
    assert isinstance(request, TurnRequest), "the forged inbound contract survived ingress"
    assert request.trust_principal == PRINCIPAL_REMOTE_UNTRUSTED, (
        "forged principal/trust_principal fields elevated a remote ingress to owner_local"
    )
    assert request.request_id == accepted["request_id"]
    # The door does not stamp a canonical turn id (the runtime writes that key only
    # later, from the persisted dialogue turn), so a door-served turn's canonical
    # request carried NO turn identity at all while the execution seam minted one of
    # its own — two turn ids for one turn, neither knowing about the other.
    assert request.turn_id, "the door-served turn's canonical request carries no turn identity"
    assert str(seen.get("trigger_user_turn_id") or "") == request.turn_id, (
        "the door-served turn's execution identity minted its own turn id instead of "
        f"consuming the request's ({seen.get('trigger_user_turn_id')!r} vs "
        f"{request.turn_id!r})"
    )


# ------------------------------------------------------------------ 5. survival vs persistence


def test_the_one_request_survives_the_real_turns_context_replacement(make_agent):
    """Driven through the REAL inner runtime (which replaces the caller's context
    wholesale at the checkpoint merge): the object the caller ends holding is the same
    object the execution seam consumed, and persistence never carries it."""
    agent = make_agent()
    context: dict = {"surface": "cli", "platform": "cli"}
    spy = _SeamSpy()
    with mock.patch.object(agent_mod, "_r3_open_turn_execution", spy):
        agent.run_once("what is 2+2?", source_context=context)

    request = context.get(TURN_REQUEST_KEY)
    assert isinstance(request, TurnRequest)
    assert spy.kwargs.get("turn_request") is request, (
        "the request that survived the checkpoint replacement is not the object "
        "execution identity was opened from"
    )
    assert TURN_REQUEST_KEY not in _stable_source_context(context), (
        "the live turn contract reached the checkpoint persistence boundary"
    )


# ------------------------------------------------------------------ 6. the measured boundary


def _incident_mocks():
    """The deterministic network stand-ins the follow-up incident fixture uses."""
    import contextlib

    from core.live_quote_contract import LiveQuoteResult
    from core.weather_result_contract import WeatherResult

    def fake_crypto(_coin_ids, **_kwargs):
        return [LiveQuoteResult(
            asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=64781.0,
            currency="USD", as_of="2026-08-06 16:20 UTC", source_label="CoinGecko",
            source_url="https://x", kind="crypto", change_percent=0.43,
        )]

    def fake_commodity(_query, _targets, **_kwargs):
        return [LiveQuoteResult(
            asset_key="gold", asset_name="Gold", symbol="GC=F", value=4320.7,
            currency="USD", as_of="2026-08-06 07:30 UTC", source_label="Yahoo Finance",
            source_url="https://x", kind="commodity", unit_label="per troy ounce",
            change_percent=0.36,
        )]

    def fake_weather(location: str, **_kwargs):
        if "atlantis" in location.lower():
            from urllib.error import HTTPError

            raise HTTPError("https://wttr.in/atlantisxyzabc123", 500, "Internal Server Error", None, None)
        return WeatherResult(
            location=location, place_label=location.title(), condition="Sunny",
            temperature_c=28.0, feels_like_c=27.0, humidity_pct=40.0, wind_kmph=8.0,
            source_label="wttr.in", source_url="https://x", observed_at="02:35 PM",
        )

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=fake_crypto))
    stack.enter_context(mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity))
    stack.enter_context(mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather))
    return stack


def test_the_answering_attempt_is_found_on_every_surface():
    """THE MEASUREMENT that bounded R1b, now inverted by R1c.

    R1b measured this arm failing: one turn owned TWO unchained attempt rows — the turn
    door's bookkeeping row and the answering lane's — and because the door closes its row
    LAST it was the session's newest-updated attempt, so the next turn's referential
    follow-up bound to the bookkeeping row and answered "No entities were recorded for
    that request". Which surface hit it was decided purely by whether the door row
    carried a session: an in-process turn hid it under "", an HTTP turn (which always
    passes a session override) did not.

    R1c made one turn one chain — the answering attempt is a typed child of the turn
    root, and follow-up resolution ranks by ROLE before recency — so both surfaces now
    find the attempt that answered. Both arms assert the same thing on purpose: surface
    parity is the property, and the HTTP arm is the one that was broken.
    """
    from tests.test_attempt_followup_resolution import (
        _INCIDENT_TEXT,
        _SOURCE_CONTEXT,
        AttemptFollowupResolutionTests,
    )

    question = "Which assets and cities did I originally ask for?"
    case = AttemptFollowupResolutionTests("test_list_original_entities_reads_from_persisted_subtasks")
    case.setUp()
    try:
        # ARM 1 — in-process shape: turn 1 passes no session override, so before R1c the
        # turn root was filed under an empty session and simply invisible to the query.
        with _incident_mocks():
            first = case.agent.run_once(_INCIDENT_TEXT, source_context=dict(_SOURCE_CONTEXT))
        in_process = case.agent.run_once(
            question, source_context=dict(_SOURCE_CONTEXT), session_id_override=first["session_id"]
        )["response"]
        for name in ("Gold", "Bitcoin", "Kaunas", "Atlantisxyzabc123"):
            assert name in in_process, (
                "the in-process follow-up lost its antecedent — the turn-door attempt "
                f"now shadows the answering one on this shape too: {in_process!r}"
            )
    finally:
        case.tearDown()

    case = AttemptFollowupResolutionTests("test_list_original_entities_reads_from_persisted_subtasks")
    case.setUp()
    try:
        # ARM 2 — HTTP shape: a session override on BOTH turns, exactly as the web door
        # passes one. Same runtime, same question, and the antecedent is already lost.
        http_session = "sess-http-shape"
        with _incident_mocks():
            case.agent.run_once(
                _INCIDENT_TEXT, source_context=dict(_SOURCE_CONTEXT), session_id_override=http_session
            )
        http = case.agent.run_once(
            question, source_context=dict(_SOURCE_CONTEXT), session_id_override=http_session
        )["response"]
        for name in ("Gold", "Bitcoin", "Kaunas", "Atlantisxyzabc123"):
            assert name in http, (
                "the HTTP-shaped follow-up lost its antecedent — it bound to the turn "
                f"root instead of the attempt that answered: {http!r}"
            )
    finally:
        case.tearDown()
