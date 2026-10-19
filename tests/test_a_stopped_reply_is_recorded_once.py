"""A reply the person stops is recorded once, at the stop, as what they saw.

Measured on the 0.7.2 train (153148eb), served daemon + real browser: the person pressed Stop in
/chat and saw "(stopped showing this answer)"; a reload right after showed no turn at all
(turn_count 0); seconds to a minute later the stopped turn finished on the server and a row landed
marked ``unsealed`` holding a draft the person never saw ("I couldn't complete my check ..."),
which every later reload and the export then showed. The stream the page had abandoned was only
closed by garbage collection, and its request-end flush wrote whatever the worker had staged.

Now the cancel door records the stopped turn before it answers -- the person's message and the
stopped marker the page shows -- and the transcript drops anything that request writes afterwards.
A Try again is a new request and records normally; a turn already sealed before the stop keeps its
sealed row and gets no second one.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from urllib.request import Request, urlopen

import pytest

import core.persistent_memory as pm
import tests._reader_served_rig as rig
from core.memory.files import conversation_log_path, load_jsonl
from core.persistent_memory import (
    append_conversation_event,
    flush_staged_conversation_events,
    has_staged_conversation_event,
    open_transcript_commit_boundary,
    persist_staged_conversation_event,
)
from core.semantic.semantic_admissions import bound_request_context

SERVED = {"surface": "web", "transcript_commit_boundary": True}
MARKER = "(stopped showing this answer)"
DRAFT = "I couldn't complete my check for whether this question has a single well-known answer"


def _rows(session_id: str) -> list[dict]:
    path = conversation_log_path()
    rows = load_jsonl(path) if path.exists() else []
    return [row for row in rows if isinstance(row, dict) and row.get("session_id") == session_id]


def _stop(request_id: str, session_id: str, question: str) -> None:
    """What the cancel door does once the person's stop was acknowledged."""
    append_conversation_event(
        session_id=session_id, user_input=question, assistant_output=MARKER, source_context=SERVED,
        stopped_request_id=request_id,
    )


def test_a_stop_before_any_answer_is_one_row_and_the_late_finish_adds_nothing() -> None:
    request_id, session = "req:test:stop-early", "s-stop-early"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session, "tell me a long story")
    rows = _rows(session)
    assert [(r["user"], r["assistant"], r["commit_state"], r["request_id"]) for r in rows] == [
        ("tell me a long story", MARKER, "stopped", request_id)
    ]
    # The stopped turn finishes later, still under its own request: its draft is not a turn.
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id=session, user_input="tell me a long story", assistant_output=DRAFT)
    assert has_staged_conversation_event(request_id) is False
    assert flush_staged_conversation_events(request_id) == 0  # the request-end flush finds nothing
    assert persist_staged_conversation_event(request_id=request_id, committed_text="Cancelled") is False
    assert len(_rows(session)) == 1
    assert DRAFT not in conversation_log_path().read_text(encoding="utf-8")


def test_a_draft_staged_before_the_stop_is_discarded_not_flushed_as_unsealed() -> None:
    request_id, session = "req:test:stop-staged", "s-stop-staged"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id=session, user_input="q", assistant_output=DRAFT)
    assert has_staged_conversation_event(request_id) is True
    _stop(request_id, session, "q")
    assert has_staged_conversation_event(request_id) is False
    assert flush_staged_conversation_events(request_id) == 0
    assert [(r["assistant"], r["commit_state"]) for r in _rows(session)] == [(MARKER, "stopped")]


def test_a_second_stop_of_the_same_turn_writes_nothing_more() -> None:
    request_id, session = "req:test:stop-twice", "s-stop-twice"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session, "q")
    _stop(request_id, session, "q")
    assert len(_rows(session)) == 1


def test_a_turn_sealed_before_the_stop_keeps_its_row_and_gets_no_second_one() -> None:
    request_id, session = "req:test:stop-after-seal", "s-stop-after-seal"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id=session, user_input="q", assistant_output="draft")
    assert persist_staged_conversation_event(request_id=request_id, committed_text="The sealed answer.") is True
    _stop(request_id, session, "q")
    assert pm._transcript_request_stopped(request_id) is False
    assert [(r["assistant"], r["commit_state"]) for r in _rows(session)] == [("The sealed answer.", "committed")]


def test_try_again_after_a_stop_is_its_own_turn_and_records_normally() -> None:
    session = "s-stop-retry"
    stopped, retry = "req:test:stop-retry-1", "req:test:stop-retry-2"
    open_transcript_commit_boundary(stopped)
    _stop(stopped, session, "q")
    open_transcript_commit_boundary(retry)
    with bound_request_context(retry):
        append_conversation_event(source_context=SERVED, session_id=session, user_input="q", assistant_output="draft 2")
    assert persist_staged_conversation_event(request_id=retry, committed_text="The answer this time.") is True
    assert [(r["assistant"], r["commit_state"], r["request_id"]) for r in _rows(session)] == [
        (MARKER, "stopped", stopped),
        ("The answer this time.", "committed", retry),
    ]


def test_without_a_request_id_nothing_is_written() -> None:
    assert pm._record_stopped_turn("", session_id="s-stop-none", user_input="q", shown_text=MARKER) == "unknown"
    assert _rows("s-stop-none") == []


def test_the_record_of_how_turns_ended_stays_bounded() -> None:
    # One real stop first, then a long session's worth of later turn endings (written through the
    # same bookkeeping the seal, the flush and the stop use).
    open_transcript_commit_boundary("req:test:bound-first")
    _stop("req:test:bound-first", "s-stop-bound", "q")
    assert pm._transcript_request_stopped("req:test:bound-first") is True
    with pm._TRANSCRIPT_STAGE_LOCK:
        for index in range(pm._TRANSCRIPT_OUTCOMES_MAX + 50):
            pm._note_transcript_outcome_locked(f"req:test:bound-{index}", pm._OUTCOME_STOPPED)
    assert len(pm._TRANSCRIPT_OUTCOMES) == pm._TRANSCRIPT_OUTCOMES_MAX
    # The newest ending is still known; the oldest have aged out.
    assert pm._transcript_request_stopped(f"req:test:bound-{pm._TRANSCRIPT_OUTCOMES_MAX + 49}") is True
    assert pm._transcript_request_stopped("req:test:bound-first") is False


# ---------------------------------------------------------------------------------------------
# The served workflow: real daemon, real /api/chat stream, real /api/chat/cancel and history.
# ---------------------------------------------------------------------------------------------

STORY = "tell me a very long story about a lighthouse keeper"
LATE = "Once upon a time a keeper named Ada (LATE DRAFT)"


class _HeldModel:
    """The scripted model holds every call for STORY until released, then answers LATE."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def __call__(self, body: dict) -> str | None:
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        last_user = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        if STORY in last_user:
            self.release.wait(60)
            return LATE
        return None


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    held = _HeldModel()
    with rig.CapturingProvider(default="Hello there.", reply_fn=held) as provider:
        daemon = rig.ServedDaemon(tmp_path_factory.mktemp("stopped-turn") / "home", provider=provider)
        daemon.start()
        # The page answers only through a model that passed its check; so does this drive.
        assert daemon.certify(timeout=180).get("state") == "verified"
        try:
            yield daemon, provider, held
        finally:
            held.release.set()
            daemon.stop()


def _post(daemon, path: str, body: dict) -> dict:
    request = Request(f"{daemon.base_url}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def _history(daemon, session: str) -> list[tuple[str, str]]:
    with urlopen(f"{daemon.base_url}/api/chat/history?session={session}", timeout=60) as response:
        payload = json.loads(response.read().decode())
    return [(m.get("role"), m.get("content")) for m in payload.get("messages", [])]


def _start_turn(daemon, provider, session: str, turn_id: str, *, read_to_end: bool):
    body = {"messages": [{"role": "user", "content": STORY}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": turn_id, "model": daemon.model, "mode": "ask"}
    seen: list[dict] = []
    calls_before = len(provider.calls)

    def _read() -> None:
        request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        response = urlopen(request, timeout=300)
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    seen.append(json.loads(line))
                if not read_to_end and stop_reading.is_set():
                    return  # the page aborts its fetch right after the cancel
        finally:
            response.close()

    stop_reading = threading.Event()
    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    deadline = time.time() + 120
    while time.time() < deadline and not any(STORY in c["text"] for c in provider.calls[calls_before:]):
        time.sleep(0.1)
    assert any(STORY in c["text"] for c in provider.calls[calls_before:]), f"the turn never reached the model: {seen[-3:]}"
    return reader, stop_reading, seen


def _wait_for_late_finish(provider, held, settle_s: float = 6.0) -> None:
    held.release.set()
    time.sleep(settle_s)
    held.release.clear()


def test_served_stop_is_in_history_at_once_and_the_late_finish_never_adds_a_draft(served) -> None:
    daemon, provider, held = served
    session = rig.canonical_session("stopped-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    reader, stop_reading, _seen = _start_turn(daemon, provider, session, turn_id, read_to_end=False)
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled"
    stop_reading.set()
    # A reload right after the stop: the turn is there, as the person saw it.
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    _wait_for_late_finish(provider, held)
    reader.join(timeout=30)
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    with urlopen(f"{daemon.base_url}/api/chat/export?format=md&session={session}", timeout=60) as response:
        exported = response.read().decode("utf-8", "replace")
    assert STORY in exported and MARKER in exported
    assert "LATE DRAFT" not in exported and "couldn't complete my check" not in exported
    rows = _rows_in(daemon, session)
    assert [row.get("commit_state") for row in rows] == ["stopped"]


def test_served_stop_with_the_client_still_reading_keeps_the_typed_cancel_and_one_row(served) -> None:
    daemon, provider, held = served
    session = rig.canonical_session("stopped-reading-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    reader, _stop_reading, seen = _start_turn(daemon, provider, session, turn_id, read_to_end=True)
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled"
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    _wait_for_late_finish(provider, held, settle_s=1.0)
    reader.join(timeout=120)
    assert not reader.is_alive(), "the stream never ended after the cancel"
    typed = [str(obj["vool_event"].get("type")) for obj in seen if obj.get("vool_event")]
    assert "task.cancelled" in typed, typed
    published = "".join(str((obj.get("message") or {}).get("content") or "") for obj in seen)
    assert "LATE DRAFT" not in published
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    assert [row.get("commit_state") for row in _rows_in(daemon, session)] == ["stopped"]
    # The worker released its registration: the stop is over, a repeat finds nothing to stop.
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "not_found"


def _rows_in(daemon, session: str) -> list[dict]:
    path = daemon.home / "data" / "conversation_log.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    return [row for row in rows if row.get("session_id") == session]
