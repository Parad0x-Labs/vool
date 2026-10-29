"""One message sent is one turn kept, holding the answer the person actually saw.

Measured on a fresh install with no model (UX review J-02): one "what is the weather like in rome"
sent from /chat -- a single POST /api/chat -- came back after a reload as the question twice, with
two different answers ("I can't publish this answer: no AI model is connected..." and "I could not
obtain a current reading for this on this turn..."), and /api/chat/sessions counted 2 turns. Live,
only the first answer had shown.

The owning cause: the front door asks the live-info lane first. With live lookup off, that lane
returns a refusal, and its result builder had already written the turn's conversation row. The
front door then sets the refusal aside (it answers nothing) and lets a later lane answer the
message, and that lane writes its own row. The seal certified one of the two rows with the
served answer; the other was flushed at request end with a draft nobody saw. A slice answer set
aside on a mixed message wrote the same extra row. The front door now holds a lane's row until it
serves that lane's result.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

import pytest

from core.agent_runtime.fast_command_surface import deferred_turn_rows, record_deferred_turn_rows
from core.memory.files import conversation_log_path, load_jsonl
from core.persistent_memory import (
    append_conversation_event,
    flush_staged_conversation_events,
    open_transcript_commit_boundary,
)
from core.semantic.semantic_admissions import bound_request_context
from tests.test_transcript_persists_at_commit import SERVED, _seal

WEATHER = "what is the weather like in rome"


def _rows(session_id: str) -> list[dict]:
    path = conversation_log_path()
    rows = load_jsonl(path) if path.exists() else []
    return [row for row in rows if isinstance(row, dict) and row.get("session_id") == session_id]


def _lane_row(session_id: str, text: str) -> None:
    from core.agent_runtime.fast_command_surface import _record_turn_row

    _record_turn_row(
        append_conversation_event,
        {"session_id": session_id, "user_input": WEATHER, "assistant_output": text, "source_context": SERVED},
    )


def test_a_set_aside_lane_result_leaves_no_turn_and_the_answering_lane_leaves_one() -> None:
    session_id = "s-one-turn-set-aside"
    request_id = "req:test:one-turn-set-aside"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        with deferred_turn_rows():
            _lane_row(session_id, "Live web lookup is disabled on this runtime...")
        # The refusal was set aside; the lane that answers the message writes the one row.
        _lane_row(session_id, "I could not obtain a current reading for this on this turn...")
        commit = _seal("The answer the person saw.", turn_id="t-one-turn-set-aside")
    assert flush_staged_conversation_events(request_id) == 0
    rows = _rows(session_id)
    assert [(row["user"], row["assistant"], row["commit_state"]) for row in rows] == [
        (WEATHER, "The answer the person saw.", "committed"),
    ]
    assert rows[0]["finalization_id"] == commit["finalization_id"]
    raw = conversation_log_path().read_text(encoding="utf-8")
    assert "Live web lookup is disabled" not in raw
    assert "could not obtain a current reading" not in raw


def test_a_served_lane_result_keeps_its_row_exactly_once() -> None:
    session_id = "s-one-turn-served"
    request_id = "req:test:one-turn-served"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        with deferred_turn_rows() as held:
            _lane_row(session_id, "Live web lookup is disabled on this runtime...")
        assert _rows(session_id) == []
        record_deferred_turn_rows(held)
        record_deferred_turn_rows(held)  # a second call has nothing left to write
        _seal("Live web lookup is disabled on this runtime...", turn_id="t-one-turn-served")
    assert flush_staged_conversation_events(request_id) == 0
    assert [(row["assistant"], row["commit_state"]) for row in _rows(session_id)] == [
        ("Live web lookup is disabled on this runtime...", "committed"),
    ]


def test_a_lane_that_raises_inside_the_hold_does_not_leave_later_rows_held() -> None:
    session_id = "s-one-turn-raise"
    with pytest.raises(RuntimeError):
        with deferred_turn_rows():
            raise RuntimeError("lane failed")
    # Outside any hold, and with no served request, a row lands at once as before.
    _lane_row(session_id, "written at once")
    assert [row["assistant"] for row in _rows(session_id)] == ["written at once"]


# ---- the real workflow: a fresh install, a real daemon, one POST /api/chat -----------------------


@pytest.fixture()
def fresh_install_daemon(tmp_path):
    from tests import _reader_served_rig as rig

    class _FreshInstall(rig.ServedDaemon):
        def register_provider(self) -> str:
            # A fresh install has no model: nothing is registered, and every local endpoint
            # points at a stand-in that lists no models.
            return ""

    with rig.CapturingProvider() as provider:
        daemon = _FreshInstall(tmp_path / "home", provider=provider,
                               env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        daemon.start(timeout=180)
        try:
            yield daemon
        finally:
            daemon.stop()


def _get(daemon, path: str) -> dict:
    with urlopen(daemon.base_url + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_chat(daemon, session_id: str, message: str, **extra) -> str:
    payload = {"messages": [{"role": "user", "content": message}], "session_id": session_id, "mode": "auto", **extra}
    request = Request(daemon.base_url + "/api/chat", data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=300) as response:
        raw = response.read().decode("utf-8")
    if not extra.get("stream"):
        return str((json.loads(raw).get("message") or {}).get("content") or "")
    text = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("done") and isinstance(event.get("message"), dict):
            text = str(event["message"].get("content") or text)
        elif isinstance(event, dict) and isinstance(event.get("message"), dict):
            text += str(event["message"].get("content") or "")
    return text


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streamed"])
def test_one_weather_question_on_a_fresh_install_is_one_turn_after_reload(fresh_install_daemon, stream):
    from tests import _reader_served_rig as rig

    daemon = fresh_install_daemon
    session_id = rig.canonical_session(f"one-turn-weather-{stream}")
    extra = {"stream": True, "stream_task_events": True} if stream else {"stream": False}
    served = _post_chat(daemon, session_id, WEATHER, **extra)
    assert served.strip(), "the person must be shown an answer"

    sessions = {row.get("id") or row.get("session_id"): row for row in _get(daemon, "/api/chat/sessions")["sessions"]}
    assert sessions[session_id]["turn_count"] == 1

    history = _get(daemon, "/api/chat/history?session=" + session_id)
    messages = [m for m in history.get("messages", []) if m.get("role") in {"user", "assistant"}]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == WEATHER
    # What a reload shows is what the person saw live.
    assert messages[1]["content"].strip() == served.strip()

    # A plain, non-live question in the same chat is one more turn, not two.
    _post_chat(daemon, session_id, "hi, how are you today?", **extra)
    sessions = {row.get("id") or row.get("session_id"): row for row in _get(daemon, "/api/chat/sessions")["sessions"]}
    assert sessions[session_id]["turn_count"] == 2


# ---- the same message through the agent itself (a surface that writes its row at once) ---------


@pytest.mark.parametrize("text", [
    WEATHER,
    "what is the weather like in rome and also what is 17 times 23?",
], ids=["weather", "weather-beside-arithmetic"])
def test_a_live_question_asked_once_is_one_turn_on_a_surface_without_a_commit_boundary(text):
    from core.persistent_memory import recent_conversation_events
    from tests.test_mixed_demand_terminal_closure_p0 import _model_stand_in
    from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

    harness = _Harness(f"sess-one-turn-{abs(hash(text)) % 10**8}")
    try:
        with _model_stand_in(harness.agent, "A stand-in answer."):
            result = harness.agent.run_once(
                text, source_context=dict(_SOURCE_CONTEXT), session_id_override=harness.session_id
            )
        rows = recent_conversation_events(harness.session_id, limit=20)
        assert [row["user"] for row in rows] == [text]
        assert rows[0]["assistant"] == str(result.get("response") or "")
    finally:
        harness.close()


def test_a_refusal_the_front_door_serves_under_local_only_is_kept_as_the_one_turn():
    """Under the Local Only composite the front door SERVES the live-info refusal (no lane could
    reach the web). The row the lane held back is then written once, with the served text."""
    from unittest import mock

    from core import policy_engine
    from core.persistent_memory import recent_conversation_events
    from tests.test_mixed_demand_terminal_closure_p0 import _model_stand_in
    from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

    harness = _Harness("sess-one-turn-local-only-refusal")
    try:
        with mock.patch.object(policy_engine, "local_only_mode", return_value=True), \
                mock.patch.object(policy_engine, "allow_web_fallback", return_value=False), \
                _model_stand_in(harness.agent, "A stand-in answer."):
            result = harness.agent.run_once(
                WEATHER, source_context=dict(_SOURCE_CONTEXT), session_id_override=harness.session_id
            )
        response = str(result.get("response") or "")
        assert "Web lookups are off, so I didn't go online" in response
        rows = recent_conversation_events(harness.session_id, limit=20)
        assert [(row["user"], row["assistant"]) for row in rows] == [(WEATHER, response)]
    finally:
        harness.close()
