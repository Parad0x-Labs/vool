"""R1c — one external turn, one turn id, one attempt chain.

WHAT THIS FILE PINS
-------------------
R1b unified the request/turn identity the EXECUTION SEAM consumes and measured what it
could not fix from there: one external turn owns TWO unchained attempt rows.

* the turn door mints a bookkeeping attempt (its own root, the ingress turn id) and
  closes it LAST, so it is the session's newest-updated row;
* the answering lane mints its own attempt (its own root, the DIALOGUE turn id, minted
  separately inside `record_dialogue_turn`), carrying the subtasks and entities.

Nothing links the two. A referential follow-up resolved as "the latest attempt in this
session" therefore binds to the bookkeeping row and answers "No entities were recorded
for that request" — live on the HTTP surface, hidden in-process only because the door
row was filed under an empty session, which is itself a broken identity.

These pins hold the repaired shape: ONE canonical turn id that is also the persisted
dialogue-turn id, every attempt of the turn filed under the request's session and that
turn id, one explicit chain rooted at the turn root, machine-readable roles, and a
follow-up resolution that selects the answer-bearing attempt STRUCTURALLY — so
reordering `updated_at` cannot change which attempt a follow-up binds to.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.runtime_continuity import (
    reset_runtime_continuity_state,
)
from core.turn_contract import TURN_REQUEST_KEY

_SOURCE_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}
_INCIDENT = "market prices for gold and bitcoin plus weather in Atlantisxyzabc123 and Kaunas"
_ENTITY_QUESTION = "Which assets and cities did I originally ask for?"


def _incident_mocks():
    """Deterministic stand-ins for every network call the incident turn makes."""
    from core.live_quote_contract import LiveQuoteResult
    from core.weather_result_contract import WeatherResult

    def fake_crypto(coin_ids, **_kwargs):
        return [LiveQuoteResult(
            asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=64781.0,
            currency="USD", as_of="2026-08-06 16:20 UTC", source_label="CoinGecko",
            source_url="https://x", kind="crypto", change_percent=0.43,
        )]

    def fake_commodity(_query, targets, **_kwargs):
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


class _Harness:
    """One agent against the runtime's OWN database.

    Deliberately not a separate continuity database: the L0 fence (`executions`) lives in
    the runtime database, so pointing continuity at a second file puts a turn's attempts
    and its fence in different stores — a shape production never has, and one that makes
    every fence predicate read an empty table. Each test uses its own session id instead.
    """

    def __init__(self, session_id: str) -> None:
        reset_runtime_continuity_state()
        self.session_id = session_id
        self.agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def close(self) -> None:
        reset_runtime_continuity_state()

    def turn(self, text: str, *, session_id: str | None = None, mocked: bool = True) -> tuple[dict, dict]:
        """One real turn. Returns (result, the context the turn ran under)."""
        context = dict(_SOURCE_CONTEXT)
        if mocked:
            with _incident_mocks():
                result = self.agent.run_once(text, source_context=context, session_id_override=session_id)
        else:
            result = self.agent.run_once(text, source_context=context, session_id_override=session_id)
        return result, context

    def attempts(self, session_id: str | None = None) -> list[dict]:
        from storage.db import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM runtime_attempts WHERE session_id = ? ORDER BY created_at ASC",
                (str(session_id or self.session_id),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def set_updated_at(self, attempt_id: str, value: str) -> None:
        from storage.db import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE runtime_attempts SET updated_at = ? WHERE attempt_id = ?",
                (value, attempt_id),
            )
            conn.commit()
        finally:
            conn.close()


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-r1c-{request.node.name[:40]}")
    try:
        yield h
    finally:
        h.close()


def _roles(attempts: list[dict]) -> list[str]:
    return [str(a.get("attempt_role") or "") for a in attempts]


# ------------------------------------------------------------------ 1. the user-visible defect


def test_the_http_shaped_followup_reports_the_original_entities(harness):
    """THE user-visible failure R1b measured and could not fix from the seam.

    Two turns with a session override on both — exactly what `core/web/api/runtime.py`
    passes on every HTTP turn. The second turn asks what the first one was about, and
    must get the entities the first turn actually recorded.
    """
    session = harness.session_id
    harness.turn(_INCIDENT, session_id=session)
    answer = harness.turn(_ENTITY_QUESTION, session_id=session, mocked=False)[0]["response"]
    for name in ("Gold", "Bitcoin", "Kaunas", "Atlantisxyzabc123"):
        assert name in answer, (
            "the follow-up lost its antecedent — it bound to the turn-door bookkeeping "
            f"row instead of the attempt that answered: {answer!r}"
        )


# ------------------------------------------------------------------ 2. one chain, one identity


def test_every_attempt_of_one_turn_shares_the_session_turn_and_root(harness):
    """One turn == one chain. Every row it writes carries the request's session, the
    canonical turn id, and the SAME root — no unrelated second root."""
    _result, context = harness.turn(_INCIDENT, session_id=harness.session_id)
    request = context[TURN_REQUEST_KEY]
    attempts = harness.attempts()
    assert len(attempts) >= 2, "expected the turn door's attempt and the answering lane's"

    sessions = {str(a["session_id"]) for a in attempts}
    assert sessions == {request.session_id}, (
        f"attempts of one turn are filed under different sessions: {sessions} "
        f"(request session {request.session_id!r})"
    )
    turn_ids = {str(a["origin_user_turn_id"]) for a in attempts}
    assert turn_ids == {request.turn_id}, (
        f"attempts of one turn carry different turn ids: {turn_ids} "
        f"(request turn id {request.turn_id!r})"
    )
    roots = {str(a["root_attempt_id"]) for a in attempts}
    assert len(roots) == 1, f"one turn produced {len(roots)} unrelated attempt roots: {roots}"

    # the root IS the turn door's own attempt, and every other row descends from it
    door_attempt_id = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    assert roots == {door_attempt_id}, (
        f"the chain root {roots} is not the turn door's attempt {door_attempt_id!r}"
    )
    roles = _roles(attempts)
    assert "turn_root" in roles, f"no attempt is typed as the turn root: {roles}"
    assert "answer" in roles, f"no attempt is typed as answer-bearing: {roles}"


def test_http_and_in_process_turns_produce_the_same_attempt_topology():
    """Surface equivalence: the same message must produce the same chain shape and the
    same session binding whether or not the caller passes a session override."""
    shapes = {}
    for label, session in (("http", "sess-r1c-parity-http"), ("in_process", None)):
        h = _Harness("sess-r1c-parity-http")
        try:
            _result, context = h.turn(_INCIDENT, session_id=session)
            request = context[TURN_REQUEST_KEY]
            # an in-process turn mints its own session; read the rows under whichever
            # session the turn actually ran as
            attempts = h.attempts(request.session_id)
            shapes[label] = {
                "roles": sorted(_roles(attempts)),
                "roots": len({str(a["root_attempt_id"]) for a in attempts}),
                "sessions_match_request": {str(a["session_id"]) for a in attempts} == {request.session_id},
                "empty_sessions": sum(1 for a in attempts if not str(a["session_id"]).strip()),
            }
        finally:
            h.close()

    assert shapes["in_process"]["empty_sessions"] == 0, (
        "an in-process turn still hides a row under an empty session: "
        f"{shapes['in_process']}"
    )
    assert shapes["http"] == shapes["in_process"], (
        f"HTTP and in-process topology differ: {shapes['http']} vs {shapes['in_process']}"
    )


# ------------------------------------------------------------------ 3. one turn id


def test_the_persisted_dialogue_turn_is_the_requests_turn_id(harness):
    """The canonical turn id is the dialogue-turn id — no second id minted later inside
    `record_dialogue_turn` while the request carries a different one."""
    from storage.dialogue_memory import recent_dialogue_turns

    _result, context = harness.turn(_INCIDENT, session_id=harness.session_id)
    request = context[TURN_REQUEST_KEY]
    recorded = [
        str(turn.get("turn_id") or "")
        for turn in recent_dialogue_turns(harness.session_id, limit=8)
    ]
    assert request.turn_id in recorded, (
        f"the request's turn id {request.turn_id!r} was never the persisted dialogue turn "
        f"(dialogue rows: {recorded}) — a second turn identity was minted downstream"
    )
    assert str(context.get("_canonical_user_turn_id") or "") == request.turn_id, (
        "the runtime's canonical turn key diverged from the request's turn id"
    )


# ------------------------------------------------------------------ 4. structural selection


def test_follow_up_selection_cannot_be_changed_by_reordering_updated_at(harness):
    """Selection is STRUCTURAL. The turn door closes its row last today, which is the
    only reason it wins 'the latest attempt in this session'. Flip the order and the
    resolver must still bind to the same answer-bearing attempt."""
    from core.attempt_followup import LIST_ORIGINAL_ENTITIES, resolve_followup_attempt

    session = harness.session_id
    _result, context = harness.turn(_INCIDENT, session_id=session)
    door_attempt_id = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    attempts = harness.attempts()
    answer_rows = [a for a in attempts if str(a["attempt_id"]) != door_attempt_id]
    assert answer_rows, "the turn recorded no answering attempt at all"
    answer_id = str(answer_rows[-1]["attempt_id"])

    resolved = []
    for door_stamp, answer_stamp in (
        ("2026-08-31T10:00:02+00:00", "2026-08-31T10:00:01+00:00"),  # door newest (today's shape)
        ("2026-08-31T10:00:01+00:00", "2026-08-31T10:00:02+00:00"),  # answer newest
    ):
        harness.set_updated_at(door_attempt_id, door_stamp)
        harness.set_updated_at(answer_id, answer_stamp)
        attempt, _reason = resolve_followup_attempt(
            session, _ENTITY_QUESTION, LIST_ORIGINAL_ENTITIES
        )
        resolved.append(str((attempt or {}).get("attempt_id") or ""))

    assert resolved[0] == resolved[1], (
        f"reordering updated_at changed which attempt the follow-up resolved: {resolved}"
    )
    assert resolved[0] == answer_id, (
        f"the follow-up resolved the bookkeeping row {resolved[0]!r} instead of the "
        f"answer-bearing attempt {answer_id!r}"
    )


# ------------------------------------------------------------------ 5. planner children


def test_planner_subturn_attempts_are_typed_children_of_the_turn_root(harness):
    """Internal planner work is one turn's work: a sub-task's attempt hangs off the SAME
    root, carries the parent turn's id and session, and is typed as planner work rather
    than as a turn of its own.

    Driven at the writer with the exact sub-turn context `build_planner_run_one` builds
    (the parent turn's own runtime context, marked `planned_subturn`).

    When this was written a planned sub-turn could not reach the live-data lane at all —
    `planned_subturn` short-circuited it, so the lane answered "Live web lookup is
    disabled on this runtime" inside a sub-turn while the same message served by the
    parent turn went through, and an end-to-end planned turn recorded no sub-task attempt
    to make a claim about. That is fixed (`tests/test_planner_chain_execution.py` drives
    the whole planned turn and asserts both children). This pin stays as the writer-level
    half: the row, when written, joins its parent turn's chain.
    """
    _result, context = harness.turn(_INCIDENT, session_id=harness.session_id)
    request = context[TURN_REQUEST_KEY]
    door_attempt_id = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    before = {str(a["attempt_id"]) for a in harness.attempts()}

    # exactly what the planner hook hands a sub-turn
    sub_context = dict(context)
    sub_context["planned_subturn"] = True
    sub_context["suppress_conversation_log"] = True
    sub_context.pop("runtime_event_stream_id", None)

    attempt_id = harness.agent._create_live_data_runtime_attempt(
        session_id=request.session_id,
        source_context=sub_context,
        effective_input="weather in Kaunas",
        answer_mode="LIVE_DATA",
    )
    assert attempt_id, "the sub-turn's lane recorded no attempt"

    children = [a for a in harness.attempts() if str(a["attempt_id"]) not in before]
    assert len(children) == 1
    child = children[0]
    assert str(child["root_attempt_id"]) == door_attempt_id, (
        f"the planner sub-task attempt is its own root, not a child of the turn root "
        f"{door_attempt_id!r}"
    )
    assert str(child["attempt_role"]) == "planner_task", (
        f"the planner sub-task attempt is typed {child.get('attempt_role')!r}"
    )
    assert str(child["origin_user_turn_id"]) == request.turn_id, (
        "a planner sub-task attempt carries a different turn id than its parent turn"
    )
    assert str(child["session_id"]) == request.session_id
    assert str(child["execution_id"]) == door_attempt_id


# ------------------------------------------------------------------ 6. retry stays on the chain


def test_a_retry_stays_on_the_turn_root_and_advances_the_generation(harness):
    """A retry is a new generation of the SAME chain — the turn's root, the turn's
    fence — not a fresh root beside it."""
    from core.attempt_retry import execute_attempt_retry
    from core.runtime_continuity import get_runtime_attempt, list_runtime_attempt_subtasks

    session = harness.session_id
    _result, context = harness.turn(_INCIDENT, session_id=session)
    door_attempt_id = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    attempts = harness.attempts()
    answer_rows = [a for a in attempts if str(a["attempt_id"]) != door_attempt_id]
    assert answer_rows, "the turn recorded no answering attempt to retry"
    parent = get_runtime_attempt(str(answer_rows[-1]["attempt_id"]))

    with _incident_mocks():
        retried = execute_attempt_retry(
            parent,
            list_runtime_attempt_subtasks(str(parent["attempt_id"])),
            session_id=session,
            checkpoint_id="",
            trigger_user_turn_id="turn-r1c-retry",
            resolution_intent="RETRY_ATTEMPT",
        )
    child = retried["attempt"]
    assert str(child["root_attempt_id"]) == door_attempt_id, (
        f"the retry left the turn's chain: root {child['root_attempt_id']!r} vs turn root "
        f"{door_attempt_id!r}"
    )
    assert int(child["execution_generation"]) == 2, (
        f"the retry did not advance the chain's generation: {child['execution_generation']}"
    )
    assert str(child.get("attempt_role") or "") == "retry"


def test_the_turn_doors_terminal_write_targets_its_own_chain(harness):
    """Finalization/cancellation act on the chain the turn opened: the door row's
    execution identity is the chain root, and its terminal state is written there."""
    _result, context = harness.turn(_INCIDENT, session_id=harness.session_id)
    identity = context.get("_execution_identity") or {}
    door_attempt_id = str(identity.get("attempt_id") or "")
    assert str(identity.get("execution_id") or "") == door_attempt_id, (
        "the turn's execution id does not alias the chain root"
    )
    attempts = harness.attempts()
    door = next(a for a in attempts if str(a["attempt_id"]) == door_attempt_id)
    assert str(door["execution_id"]) == door_attempt_id
    assert str(door["lifecycle_state"]) in {"SUCCEEDED", "PARTIAL_SUCCESS"}
    assert str(door["attempt_role"]) == "turn_root"
    # every other row of the turn shares that execution identity
    for other in attempts:
        assert str(other["execution_id"]) == door_attempt_id, (
            f"attempt {other['attempt_id']} is fenced under a different execution "
            f"({other['execution_id']!r})"
        )


# ------------------------------------------------------------------ 7. exclusivity still holds


def test_the_chain_container_and_its_executor_coexist_but_two_executors_do_not(tmp_path):
    """INV-2 (executing exclusivity) after R1c split the key.

    A turn's root row is live for the whole turn by construction, so keyed on
    `execution_id` alone it collided with its own turn's answering attempt — the claim was
    refused and the turn served "could not be retrieved". The key now carries one extra
    bit separating the chain's CONTAINER from its EXECUTORS, so the pair coexists while
    the invariant the index was written for is untouched: two live executing attempts on
    one chain are still refused by the database, not by a caller's good manners.
    """
    import sqlite3

    from storage.migrations import run_migrations

    db = tmp_path / "inv2.db"
    run_migrations(db_path=db)
    run_migrations(db_path=db)  # idempotent: the rebuild must survive a second pass

    conn = sqlite3.connect(str(db))
    try:
        def _insert(attempt_id: str, role: str, state: str) -> None:
            conn.execute(
                """
                INSERT INTO runtime_attempts (
                    attempt_id, session_id, execution_id, attempt_role,
                    lifecycle_state, created_at, updated_at
                ) VALUES (?, 's', 'exec-1', ?, ?, '2026-08-31T00:00:00Z', '2026-08-31T00:00:00Z')
                """,
                (attempt_id, role, state),
            )
            conn.commit()

        _insert("a-root", "turn_root", "RUNNING")
        _insert("a-answer", "answer", "RUNNING")  # the container and its executor coexist

        with pytest.raises(sqlite3.IntegrityError):
            _insert("a-second-executor", "planner_task", "RUNNING")

        # and a second live ROOT on the same chain is refused too
        with pytest.raises(sqlite3.IntegrityError):
            _insert("a-second-root", "turn_root", "PLANNED")
    finally:
        conn.close()
