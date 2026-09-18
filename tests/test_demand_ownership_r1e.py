"""R1e — canonical demand ownership BEFORE lane execution.

WHAT THIS FILE PINS
-------------------
R1d proved a planned turn's children are real (chain, slots, recall) but had to FORCE the
live-data lane to decline the parent message, because at base that lane claims every
message carrying a live entity — whole — before the planner sees it. Measured at base
1543a597, hermetic, with deterministic fetch stand-ins:

    "Explain how a hash table works and tell me the weather in Kaunas"
      -> "Kaunas: Sunny, 28 C."                        (the explanation VANISHED)
    "convert 100 usd to eur and explain what a hedge fund is"
      -> "100 USD ... = 85.97 EUR ..."                 (the explanation VANISHED)
    "weather in Kaunas and Rome plus explain inflation briefly"
      -> the two-city weather table                    (the reasoning VANISHED)
    "tell me the weather in Atlantisxyzabc123 and explain entropy briefly"
      -> a weather table row reading "Explain Entropy Briefly | Sunny | 28"
         (the explanation became a CITY and was served fake weather)

THE CONTRACT UNDER TEST
-----------------------
The turn's demand units are minted from the request BEFORE execution. A deterministic
lane may END the external turn only when it accounts for EVERY unit; a lane that covers
one unit of several proposes that coverage and the runtime executes every covered unit
through its owning lane, then synthesizes ONE answer that states partial failure
truthfully. Pure turns keep their existing paths: a single-unit live request keeps the
direct fast path, a pure general request keeps the normal answer path, and a city name
alone admits no live-data lane at all.

THE STAND-INS (both named, both narrow)
---------------------------------------
1. `render_response` — the general answer's renderer is patched to a fixed text, the
   same seam tests/test_agent_runtime_turn_reasoning.py already uses. Everything around
   it is real: routing, the lanes, sub-turns, attempts, the merge.
2. The network fetches — the R1c incident mocks (weather/market) plus a context-supplied
   FX fetcher for the conversion case. No socket is opened.
"""
from __future__ import annotations

from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.turn_contract import TURN_REQUEST_KEY
from tests.test_turn_attempt_chain import _Harness, _incident_mocks

_MIXED_EXPLAIN_WEATHER = "Explain how a hash table works and tell me the weather in Kaunas"
_MIXED_CONVERT_EXPLAIN = "convert 100 usd to eur and explain what a hedge fund is"
_MIXED_TWO_LIVE_GENERAL = "weather in Kaunas and Rome plus explain inflation briefly"
_MIXED_FAILURE_SUCCESS = (
    "tell me the weather in Atlantisxyzabc123 and Kaunas plus explain entropy briefly"
)
_NEAR_MISS = "Tell me about the history of Kaunas"
_PURE_LIVE = "weather in Kaunas"
_PURE_GENERAL = "explain hash tables"

_HASH_STAND_IN = "HASH-TABLE-STAND-IN: a hash table maps keys to array buckets via a hash function."
_HEDGE_STAND_IN = "HEDGE-FUND-STAND-IN: a hedge fund pools capital for actively managed positions."
_INFLATION_STAND_IN = "INFLATION-STAND-IN: inflation is a sustained rise in the general price level."
_ENTROPY_STAND_IN = "ENTROPY-STAND-IN: entropy measures the disorder of a system."
_GENERAL_STAND_IN = "GENERAL-STAND-IN: an ordinary answer from the normal reasoning path."


def _model_stand_in(agent, text: str):
    """Deterministic general answers through the REAL model lane.

    The router's own decision type (`ModelExecutionDecision`) is returned with
    `used_model=True` and a fixed `output_text`, so the ordinary reasoning path runs
    end to end — routing, guards, receipts — and serves the stand-in as its model-final
    wording. The renderer is patched to the same text belt-and-braces: whichever of the
    two wording seams the surface takes, the reply is the stand-in. No provider socket.
    """
    import contextlib

    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model",
        task_hash="r1e-stand-in",
        provider_id="r1e-stand-in",
        provider_name="R1E stand-in",
        model_name="r1e-stand-in",
        output_text=text,
        confidence=0.9,
        trust_score=0.9,
        used_model=True,
    )
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch.object(agent.memory_router, "resolve", return_value=decision)
    )
    stack.enter_context(
        mock.patch("core.agent_runtime.agent.render_response", return_value=text)
    )
    return stack


def _fx_stand_in(rate: str = "0.86000") -> dict[str, object]:
    """A context-supplied FX fetcher: the front door's own injection point, no socket."""

    def _fetch(_url, _timeout, _headers):
        return {"rate": rate, "date": "2026-08-31"}

    return {"fx_fetch_json": _fetch, "fx_now": None}


def _turn_with(harness: _Harness, text: str, *, extra: dict[str, object]) -> tuple[dict, dict]:
    """One real turn whose source context carries the FX stand-in (the R1c harness has
    no context-extra hook; this is the same run_once call it makes, one key richer)."""
    from tests.test_turn_attempt_chain import _SOURCE_CONTEXT

    context = dict(_SOURCE_CONTEXT)
    context.update(extra)
    with _incident_mocks():
        result = harness.agent.run_once(
            text, source_context=context, session_id_override=harness.session_id
        )
    return result, context


def _weather_fetch_recorder():
    """(calls, context-manager) — records every weather fetch the turn makes."""
    calls: list[str] = []
    real = None
    try:  # keep the happy path intact for lanes that legitimately fetch
        from tools.web import web_research

        real = web_research.structured_weather_lookup
    except Exception:
        real = None

    def _recording(location: str, **kwargs):
        calls.append(str(location))
        if real is not None:
            return real(location, **kwargs)
        return None

    return calls, mock.patch(
        "tools.web.web_research.structured_weather_lookup", side_effect=_recording
    )


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-r1e-{request.node.name[:44]}")
    try:
        yield h
    finally:
        h.close()


def _children(harness: _Harness, context: dict, *, session_id: str | None = None) -> list[dict]:
    door = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    return [a for a in harness.attempts(session_id) if str(a["attempt_id"]) != door]


# ------------------------------------------------- 1. the user-visible defect (RED)


def test_mixed_explanation_and_weather_loses_neither_demand(harness):
    """THE R1e incident. A message with one live-data entity and one general demand must
    answer BOTH: the live lane serves its unit, the normal path serves the other, and the
    reply is one synthesis. At base the live-data lane claimed the whole turn and the
    explanation vanished."""
    with _incident_mocks(), _model_stand_in(harness.agent, _HASH_STAND_IN):
        result, _context = harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    assert "Kaunas" in answer and "Sunny" in answer, (
        f"the live half of the mixed turn was not served: {answer!r}"
    )
    assert "HASH-TABLE-STAND-IN" in answer, (
        f"the explanation half of the mixed turn vanished — a lane that covers one demand "
        f"unit claimed the whole external turn: {answer!r}"
    )


def test_mixed_conversion_and_explanation_loses_neither_demand(harness):
    """The same defect through the currency family: a conversion clause plus an
    explanation clause. At base the whole turn was answered with the conversion lane's
    own reply and the explanation vanished.

    The conversion unit's served text is env-honest: under the test policy's disabled
    web fallback it names the retrieval disability rather than inventing a rate, on a
    network-enabled runtime it converts. Either way the unit was ANSWERED BY ITS LANE,
    and the explanation must survive beside it."""
    with _model_stand_in(harness.agent, _HEDGE_STAND_IN):
        result, _context = _turn_with(
            harness, _MIXED_CONVERT_EXPLAIN, extra=_fx_stand_in()
        )

    answer = str(result.get("response") or "")
    conversion_served = "EUR" in answer or "exchange rate" in answer.lower()
    assert conversion_served, f"the conversion half of the mixed turn was not served: {answer!r}"
    assert "HEDGE-FUND-STAND-IN" in answer, (
        f"the explanation half of the mixed turn vanished behind the currency claim: {answer!r}"
    )


def test_two_live_demands_plus_general_reasoning_serves_all_three(harness):
    """Two DISTINCT live demands (two cities) AND a general reasoning demand. All three
    must survive: both cities' data and the reasoning. At base the reply carried the
    two-city weather table and nothing else."""
    with _incident_mocks(), _model_stand_in(harness.agent, _INFLATION_STAND_IN):
        result, _context = harness.turn(_MIXED_TWO_LIVE_GENERAL, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    for token in ("Kaunas", "Rome"):
        assert token in answer, f"the {token} live demand was lost: {answer!r}"
    assert "INFLATION-STAND-IN" in answer, (
        f"the general reasoning demand of a live-heavy turn vanished: {answer!r}"
    )


def test_successful_output_survives_a_failed_sibling(harness):
    """Invariant 8, and the sharpest base defect: one live unit that FAILS (the fetch
    raises) beside one general unit that succeeds. The failure must not erase the
    success, the reply must state the partial failure truthfully, and — the measured
    base behaviour — the explanation clause must never be served as a fake weather
    row for a city named "Explain Entropy Briefly"."""
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
        result, _context = harness.turn(_MIXED_FAILURE_SUCCESS, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    assert "ENTROPY-STAND-IN" in answer, (
        f"the successful general unit was erased by its failing sibling: {answer!r}"
    )
    assert "Atlantisxyzabc123" in answer, (
        f"the failed live unit is not named in the reply — partial failure must be "
        f"truthful, not silent: {answer!r}"
    )
    assert "Explain Entropy" not in answer.replace("ENTROPY-STAND-IN", ""), (
        f"the explanation clause was served as live data (a city named by the user's own "
        f"question text): {answer!r}"
    )


# ------------------------------------------------- 2. adversarial controls (PINS)


def test_near_miss_city_name_without_live_request_never_fetches(harness):
    """A city name alone is not a live-data request. "Tell me about the history of
    Kaunas" must take the normal answer path and open zero weather fetches — the
    demand-ownership probes read the UNIT's own request, never the presence of an
    entity."""
    calls, recorder = _weather_fetch_recorder()
    with recorder, _model_stand_in(harness.agent, _GENERAL_STAND_IN):
        result, _context = harness.turn(_NEAR_MISS, session_id=harness.session_id)

    assert not calls, f"a near-miss with no live-data request fetched weather: {calls}"
    assert "GENERAL-STAND-IN" in str(result.get("response") or "")


def test_pure_live_request_keeps_the_direct_fast_path(harness):
    """Invariant 4: one live demand, one direct answer — no unit plan, no children, the
    same fast-path reply the runtime served before this contract existed."""
    with _incident_mocks():
        result, context = harness.turn(_PURE_LIVE, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    assert "Kaunas" in answer and "Sunny" in answer, answer
    roles = [str(a["attempt_role"]) for a in _children(harness, context)]
    assert "planner_task" not in roles, (
        f"a pure live request was decomposed into unit children ({roles}) — the direct "
        f"fast path must survive the demand-ownership contract unchanged"
    )


def test_pure_general_request_keeps_the_normal_answer_path(harness):
    """Invariant 5: no live demand, no decomposition — the ordinary reasoning lane
    answers, and no live fetch fires."""
    calls, recorder = _weather_fetch_recorder()
    with recorder, _model_stand_in(harness.agent, _GENERAL_STAND_IN):
        result, context = harness.turn(_PURE_GENERAL, session_id=harness.session_id)

    assert "GENERAL-STAND-IN" in str(result.get("response") or "")
    assert not calls, calls
    roles = [str(a["attempt_role"]) for a in _children(harness, context)]
    assert "planner_task" not in roles, (
        f"a pure general request was decomposed into unit children ({roles}) — the "
        f"normal answer path must survive the demand-ownership contract unchanged"
    )


# ------------------------------------------------- 3. the contract's shape


def test_one_external_turn_owns_one_request_one_user_row_and_one_root(harness):
    """Invariants 1 and 7: a mixed turn is ONE external turn — one canonical request,
    one user dialogue row, one turn root — and every child it executes for a unit shares
    that identity with its own stable execution slot."""
    with _incident_mocks(), _model_stand_in(harness.agent, _HASH_STAND_IN):
        _result, context = harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    request = context[TURN_REQUEST_KEY]
    assert isinstance(request, object) and str(request.turn_id), "no canonical TurnRequest"

    from storage.dialogue_memory import recent_dialogue_turns

    user_rows = [
        row for row in recent_dialogue_turns(harness.session_id, limit=16)
        if str(row.get("speaker_role") or "user") == "user"
    ]
    ids = [str(row.get("turn_id") or "") for row in user_rows]
    assert ids == [request.turn_id], (
        f"one external mixed turn wrote {len(ids)} user dialogue rows ({ids})"
    )

    door = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    children = _children(harness, context)
    assert children, "the mixed turn executed no child attempts for its demand units"
    for child in children:
        assert str(child["root_attempt_id"]) == door, (
            f"a demand-unit child detached from the turn root: {child['attempt_id']} "
            f"root={child['root_attempt_id']!r} door={door!r}"
        )
        assert str(child["origin_user_turn_id"]) == request.turn_id
        assert str(child["session_id"]) == request.session_id
    slots = {str(child["execution_slot"]) for child in children}
    assert len(slots) == len(children), (
        f"demand-unit children share execution slots: {slots}"
    )


def test_children_of_a_mixed_turn_stay_attached_to_the_turn_root(harness):
    """Sabotage pin (detach a child): every attempt the mixed turn writes is fenced
    under the SAME execution id, whatever its role."""
    with _incident_mocks(), _model_stand_in(harness.agent, _HASH_STAND_IN):
        _result, context = harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    door = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    for attempt in harness.attempts():
        assert str(attempt["execution_id"]) == door, (
            f"attempt {attempt['attempt_id']} is fenced under {attempt['execution_id']!r}, "
            f"not the turn root {door!r}"
        )


def test_the_live_lane_refuses_to_claim_a_mixed_turn_whole(harness):
    """Invariant 3 as a structural law on the lane itself: asked to serve a message with
    two demand units of which it covers one, the live-data lane must decline the WHOLE
    turn (it may propose its unit) — never return a whole-turn claim that drops the
    other unit. At base this exact call returned the weather-only answer."""
    with _incident_mocks():
        claim = harness.agent._maybe_answer_live_data_turn(
            effective_input=_MIXED_EXPLAIN_WEATHER,
            raw_input=_MIXED_EXPLAIN_WEATHER,
            session_id=harness.session_id,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    assert claim is None, (
        f"the live-data lane claimed a two-unit mixed turn whole and answered: "
        f"{str((claim or {}).get('response') or '')[:120]!r}"
    )


def test_no_demand_unit_executes_twice(harness):
    """Invariant 9 (execution half): each demand unit is dispatched exactly once.

    Counted at the one seam every sub-turn crosses — `_run_once_inner` — so the law is
    unit-agnostic: no unit may be swallowed (dispatched zero times by a whole-turn
    claim) and no unit may be dispatched twice (a duplicated plan task), whatever lane
    serves it."""
    from collections import Counter

    dispatched: list[str] = []
    real = VoolAgent._run_once_inner

    def _counting(self, user_input, **kwargs):
        dispatched.append(str(user_input or ""))
        return real(self, user_input, **kwargs)

    with _incident_mocks(), _model_stand_in(harness.agent, _HASH_STAND_IN):
        with mock.patch.object(VoolAgent, "_run_once_inner", _counting):
            harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    counts = Counter(text for text in dispatched if text != _MIXED_EXPLAIN_WEATHER)
    assert counts, "no demand unit was ever dispatched as its own sub-turn"
    duplicated = sorted(text for text, n in counts.items() if n > 1)
    assert not duplicated, (
        f"a demand unit was dispatched more than once (once per unit is the contract): "
        f"{[t[:60] for t in duplicated]}; all dispatch counts: "
        f"{ {text[:40]: n for text, n in counts.items()} }"
    )


def test_followup_recall_sees_the_complete_synthesized_turn(harness):
    """Invariant 10: the turn's persisted answer — what the next turn's history
    augmentation reads — is the COMPLETE synthesis, carrying both the live half and the
    general half. At base the logged reply was the weather line alone."""
    with _incident_mocks(), _model_stand_in(harness.agent, _HASH_STAND_IN):
        harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    from core.persistent_memory import augment_history_from_session_log

    history = augment_history_from_session_log(
        None, session_id=harness.session_id, user_text="and what about the other part?"
    )
    assistant_text = " ".join(
        str(entry.get("content") or "") for entry in history
        if str(entry.get("role") or "").lower() == "assistant"
    )
    assert "Kaunas" in assistant_text and "Sunny" in assistant_text, (
        f"recall history lost the live half of the synthesized turn: {assistant_text!r}"
    )
    assert "HASH-TABLE-STAND-IN" in assistant_text, (
        f"recall history lost the general half of the synthesized turn: {assistant_text!r}"
    )


# ======================================================================
# R1e AMENDMENT — executed demands must never fall through and run again
# ======================================================================
# At 137f2a79 the seam wrapped classification, dispatch, merge and finalization in
# one broad `except Exception -> None`, and also returned None when no child
# succeeded. Once `run_plan` has dispatched ANY child, None hands the external
# request back to the ordinary cascade, which repeats model calls, tools and
# effects. These tests count the real seams — `_run_once_inner` dispatches,
# `memory_router.resolve` calls, weather fetches, observation identities — never
# only final prose.

_THREE_UNIT = "weather in Kaunas. What is the price of gold? Also explain entropy briefly."


def _counting_model(agent, text: str):
    """The model stand-in with a call counter attached to `memory_router.resolve`."""
    import contextlib

    from core.memory_first_router import ModelExecutionDecision

    calls: list[dict] = []
    decision = ModelExecutionDecision(
        source="model", task_hash="r1e-amend", provider_id="r1e-amend",
        provider_name="R1E stand-in", model_name="r1e-amend",
        output_text=text, confidence=0.9, trust_score=0.9, used_model=True,
    )

    def _resolve(*args, **kwargs):
        calls.append({"args": len(args), "kwargs": sorted(kwargs)})
        return decision

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(agent.memory_router, "resolve", side_effect=_resolve))
    stack.enter_context(
        mock.patch("core.agent_runtime.agent.render_response", return_value=text)
    )
    return calls, stack


def _counting_incident_mocks():
    """The R1c incident stand-ins with invocation counters built INTO the fakes.

    Single-layer patching on purpose: stacking a counter patch over
    `_incident_mocks`' own patch on the same attribute does not reliably intercept
    the consumer (measured while writing this file), so the counter lives inside
    the fake itself and every fetch is counted exactly once."""
    import contextlib
    from urllib.error import HTTPError

    from core.live_quote_contract import LiveQuoteResult
    from core.weather_result_contract import WeatherResult

    stats: dict[str, object] = {"weather": [], "crypto": 0, "commodity": 0}

    def fake_crypto(coin_ids, **_kwargs):
        stats["crypto"] += 1
        return [LiveQuoteResult(
            asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=64781.0,
            currency="USD", as_of="2026-08-06 16:20 UTC", source_label="CoinGecko",
            source_url="https://x", kind="crypto", change_percent=0.43,
        )]

    def fake_commodity(_query, targets, **_kwargs):
        stats["commodity"] += 1
        return [LiveQuoteResult(
            asset_key="gold", asset_name="Gold", symbol="GC=F", value=4320.7,
            currency="USD", as_of="2026-08-06 07:30 UTC", source_label="Yahoo Finance",
            source_url="https://x", kind="commodity", unit_label="per troy ounce",
            change_percent=0.36,
        )]

    def fake_weather(location: str, **_kwargs):
        stats["weather"].append(str(location))
        if "atlantis" in str(location).lower():
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
    return stats, stack


def _served_reason(result: dict) -> str:
    """The reason a turn was finalized under — `reason` when the envelope carries
    it, else the `deterministic:<reason>` route label the chat surface serves."""
    reason = str(result.get("reason") or "")
    route = str(result.get("route") or "")
    if not reason and route.startswith("deterministic:"):
        return route.split(":", 1)[1]
    return reason


def _child_dispatch_recorder():
    """Counts every `_run_once_inner` dispatch made as a planned sub-turn."""
    child_texts: list[str] = []
    real = VoolAgent._run_once_inner

    def _record(self, user_input, **kwargs):
        if bool((kwargs.get("source_context") or {}).get("planned_subturn")):
            child_texts.append(str(user_input or ""))
        return real(self, user_input, **kwargs)

    return child_texts, mock.patch.object(VoolAgent, "_run_once_inner", _record)


def _parent_rerun_recorder(parent_text: str):
    """Counts executions of the PARENT text by every lane below the demand-owned seam.

    The signature of the fall-through defect is the original request re-entering a
    lane after its units already ran. Fetch counting is cache-confounded (the
    fresh-data layer serves earlier observations without a socket), so this counts
    LANE EXECUTIONS: the live-data lane, the model-split planner, the front door,
    and the grounded model turn, filtered to the parent's own text — sub-turns pass
    unit texts and never count."""
    import contextlib

    reruns: list[tuple[str, str]] = []
    watched = (
        ("live_data", "_maybe_answer_live_data_turn", "effective_input"),
        ("planner", "_maybe_answer_planned_turn", "effective_input"),
        ("frontdoor", "_handle_turn_frontdoor", "effective_input"),
        ("model_lane", "_execute_grounded_turn", "effective_input"),
    )
    stack = contextlib.ExitStack()
    for lane, attr, field in watched:
        real = getattr(VoolAgent, attr)

        def _make(lane_name, real_fn, field_name):
            def _watch(self, **kwargs):
                if str(kwargs.get(field_name) or "") == parent_text:
                    reruns.append((lane_name, parent_text[:30]))
                return real_fn(self, **kwargs)

            return _watch

        stack.enter_context(mock.patch.object(VoolAgent, attr, _make(lane, real, field)))
    return reruns, stack


def test_a_merge_failure_after_children_ran_never_reruns_the_turn(harness):
    """Requirements 3 and 6. The first child answers through the model lane, the
    second serves live weather, then `merge_outcomes` RAISES. The seam must keep
    the turn: synthesize from the outcomes it already holds, and never hand the
    parent text back to the cascade. At base the broad except returned None and
    the lanes below re-executed the original request."""
    model_calls, model_stack = _counting_model(harness.agent, _HASH_STAND_IN)
    reruns, rerun_stack = _parent_rerun_recorder(_MIXED_EXPLAIN_WEATHER)
    children, child_patch = _child_dispatch_recorder()
    with _incident_mocks(), model_stack, rerun_stack, child_patch, mock.patch(
        "core.agent_runtime.turn_planner.merge_outcomes",
        side_effect=RuntimeError("merge exploded"),
    ):
        result, _context = harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    assert len(children) == 2, f"expected one dispatch per unit, got {children}"
    assert len(model_calls) == 1, (
        f"the model was invoked {len(model_calls)} times — the children performed "
        f"their work exactly once and no lane may repeat it"
    )
    assert not reruns, (
        f"after children already ran, the failed merge handed the external request "
        f"back to the cascade and it was re-executed by: {reruns}"
    )
    answer = str(result.get("response") or "")
    assert "HASH-TABLE-STAND-IN" in answer and "Kaunas" in answer, (
        f"the outcomes the children produced were lost behind the merge failure: {answer!r}"
    )
    assert _served_reason(result).startswith("demand_owned_mixed_turn"), (
        f"the turn was finalized by {_served_reason(result)!r}, not by the demand-owned seam"
    )


def test_all_children_failed_answers_with_a_typed_failure_not_a_fallback(harness):
    """Requirement 5. Every unit's child fails. The seam must return ONE
    deterministic typed failure summary naming every unmet unit — the original
    request is never re-executed through another lane. At base `not any(ok) ->
    None` handed the turn to the cascade, which re-ran the parent text."""
    model_calls, model_stack = _counting_model(harness.agent, _HASH_STAND_IN)
    reruns, rerun_stack = _parent_rerun_recorder(_MIXED_EXPLAIN_WEATHER)
    attempts: list[str] = []

    def _broken_run_one(*_args, **_kwargs):
        def _raise(task, _done):
            attempts.append(str(getattr(task, "request", "") or ""))
            raise RuntimeError(f"child failed: {getattr(task, 'request', '')[:30]}")

        return _raise

    with _incident_mocks(), model_stack, rerun_stack, mock.patch(
        "core.agent_runtime.turn_planner_hook.build_planner_run_one",
        side_effect=_broken_run_one,
    ):
        result, _context = harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    assert len(attempts) == 2 and len(set(attempts)) == 2, (
        f"each unit must be attempted exactly once (attempted: {attempts})"
    )
    assert not reruns, (
        f"the all-failed plan fell back to the cascade and the external request was "
        f"re-executed by: {reruns}"
    )
    assert not model_calls, (
        f"the failed plan reached the model lane ({len(model_calls)} calls)"
    )
    assert _served_reason(result) == "demand_owned_mixed_turn_failed", (
        f"the all-failed turn finalized as {_served_reason(result)!r}, not the typed "
        f"failure summary"
    )
    answer = str(result.get("response") or "")
    assert "hash table" in answer.lower() and "weather in kaunas" in answer.lower(), (
        f"the typed failure must name every unmet unit: {answer!r}"
    )


def test_childrens_distinct_evidence_all_reaches_the_parent(harness):
    """Requirement 7. Two live children publish DISTINCT observation identities
    (a weather lookup and a market quote). The parent turn's evidence channel must
    carry BOTH. At base the first-writer-wins propagation kept the first child's
    observation and silently discarded the second's."""
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
        _result, context = harness.turn(_THREE_UNIT, session_id=harness.session_id)

    observations = list(context.get("runtime_tool_observations") or [])
    intents = {str(item.get("intent") or "") for item in observations}
    assert "turn.slice_answer.live_info" in intents, (
        f"the weather child's observation never reached the parent: {intents}"
    )
    assert "turn.slice_answer.market_quote" in intents, (
        f"the gold child's observation was discarded by first-writer-wins "
        f"propagation — later children's evidence must merge cumulatively: {intents}"
    )
    receipt_kinds = {
        str(item.get("kind") or "")
        for item in (context.get("web_retrieval_receipts") or [])
    }
    assert "live_data_weather_lookup" in receipt_kinds, receipt_kinds
    assert "live_data_market_quote" in receipt_kinds, receipt_kinds


def test_a_pre_dispatch_classifier_failure_declines_with_zero_child_work(harness):
    """Requirement 2. A classification/probe failure BEFORE any dispatch may
    decline with no side effects: zero unit children dispatched, and the ordinary
    cascade serves the turn exactly as it would have."""
    children, child_patch = _child_dispatch_recorder()
    with _incident_mocks(), child_patch, mock.patch(
        "core.agent_runtime.demand_ownership.demand_coverage",
        side_effect=ValueError("classifier exploded"),
    ):
        result, _context = harness.turn(_MIXED_EXPLAIN_WEATHER, session_id=harness.session_id)

    assert not children, (
        f"a pre-dispatch classifier failure dispatched unit children anyway: {children}"
    )
    assert str(result.get("response") or "").strip(), "the turn never produced an answer"


# ---------------------------------------------------------------------------------------
# The workspace_write coverage capability, and the import that keeps it alive.
#
# Found by the lint step of the pre-demo integration gate, not by this suite: the module used
# `re.match` for its continuation-marker guard while never importing `re`, so every call raised
# NameError into `except Exception: return False`. The capability answered False for every input
# — a dead coverage lane that no test noticed, because no test asked it anything.
# ---------------------------------------------------------------------------------------


def test_workspace_write_coverage_claims_a_literal_write_unit() -> None:
    """A plain literal write unit is exactly what the zero-model write lane can serve."""
    from core.agent_runtime.demand_ownership import _workspace_write_covers

    assert _workspace_write_covers("create a file called notes.txt containing hello") is True, (
        "the workspace_write coverage capability claims nothing — if it answers False for a "
        "plain literal write, the capability is dead (a swallowed NameError does exactly this)"
    )


def test_workspace_write_coverage_declines_a_continuation_fragment() -> None:
    """The guard that needs `re`: a unit OPENING with a continuation marker is a fragment of one
    composite chain, and claiming it shreds a chain a single lane serves whole.

    This is the assertion the missing import silenced. With `re` absent the function returned
    False for everything, so this test passed for the wrong reason — which is why it is paired
    with the claim test above: together they can only both hold when `re` is actually imported.
    """
    from core.agent_runtime.demand_ownership import _workspace_write_covers

    assert _workspace_write_covers("Then create summary.txt containing the totals") is False
    assert _workspace_write_covers("And also create summary.txt containing the totals") is False


def test_demand_ownership_module_imports_re() -> None:
    """Named directly, so the cause is unmissable if the import is dropped again."""
    import core.agent_runtime.demand_ownership as module

    assert getattr(module, "re", None) is not None, (
        "core.agent_runtime.demand_ownership uses re.match in _workspace_write_covers; without "
        "the import every call raises NameError into a broad except and the capability dies"
    )
