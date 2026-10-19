"""A reply the person stops is recorded once, at the stop, and never as an answer.

Measured on the 0.7.2 train (153148eb), served daemon + real browser: the person pressed Stop in
/chat and saw "(stopped showing this answer)"; a reload right after showed no turn at all
(turn_count 0); seconds to a minute later the stopped turn finished on the server and a row landed
marked ``unsealed`` holding a draft the person never saw ("I couldn't complete my check ..."),
which every later reload, the export and the next prompts then carried. The stream the page had
abandoned was closed late, and its request-end flush wrote whatever the worker had staged.

Now the cancel door records the stopped turn before it sets the cancel: the person's message, an
EMPTY assistant side, ``commit_state: "stopped"``. The transcript drops anything that request
writes afterwards, a seal or flush can never turn the stop into a recorded answer, and the
request-end flush of a stream that ended before its turn waits for the turn. Where the transcript
is shown (history, export) the page's marker is painted from the state; where it feeds a prompt
(hydration, the client-held history) the exchange is a typed closed exchange, never the model's
own reply. A Try again is a new request and records normally; a turn sealed before the stop keeps
its sealed row.
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
from core.context_history_authority import CLOSED_EXCHANGE_NOTICE
from core.memory.files import conversation_log_path, load_jsonl
from core.persistent_memory import (
    append_conversation_event,
    augment_history_from_session_log,
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


def _shape(session_id: str) -> list[tuple[str, str, str | None]]:
    return [(r["user"], r["assistant"], r.get("commit_state")) for r in _rows(session_id)]


def _stop(request_id: str, session_id: str, question: str = "q") -> None:
    """What the cancel door does once the person pressed Stop."""
    append_conversation_event(
        session_id=session_id, user_input=question, assistant_output="", source_context=SERVED,
        stopped_request_id=request_id,
    )


def _late_draft(request_id: str, session_id: str, text: str = DRAFT) -> None:
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id=session_id, user_input="q", assistant_output=text)


def test_a_stop_before_any_answer_is_one_empty_stopped_row_and_the_late_finish_adds_nothing() -> None:
    request_id, session = "req:test:stop-early", "s-stop-early"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session, "tell me a long story")
    assert _shape(session) == [("tell me a long story", "", "stopped")]
    assert _rows(session)[0]["request_id"] == request_id
    _late_draft(request_id, session)
    assert has_staged_conversation_event(request_id) is False
    assert persist_staged_conversation_event(request_id=request_id, committed_text="Cancelled") is False
    assert flush_staged_conversation_events(request_id, request_end=True) == 0
    assert _shape(session) == [("tell me a long story", "", "stopped")]
    assert DRAFT not in conversation_log_path().read_text(encoding="utf-8")


def test_a_draft_staged_before_the_stop_is_discarded_not_flushed_as_unsealed() -> None:
    request_id, session = "req:test:stop-staged", "s-stop-staged"
    open_transcript_commit_boundary(request_id)
    _late_draft(request_id, session)
    assert has_staged_conversation_event(request_id) is True
    _stop(request_id, session)
    assert has_staged_conversation_event(request_id) is False
    assert flush_staged_conversation_events(request_id, request_end=True) == 0
    assert _shape(session) == [("q", "", "stopped")]


def test_a_writer_racing_the_stop_cannot_stage_a_draft_that_is_then_sealed() -> None:
    # Review probe: the writer passed its checks before the stop and stages after it.
    request_id, session = "req:test:stop-race", "s-stop-race"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session)
    staged = pm._stage_conversation_event(request_id, {
        "session_id": session, "user_input": "q", "assistant_output": DRAFT, "source_context": SERVED,
        "response_class": None, "access_policy": None, "request_id": request_id,
    })
    assert staged == "dropped"
    assert persist_staged_conversation_event(request_id=request_id, committed_text=DRAFT) is False
    assert flush_staged_conversation_events(request_id, commit_state=pm.TRANSCRIPT_COMMIT_STATE_NO_ANSWER, assistant_text="") == 0
    assert pm._transcript_request_stopped(request_id) is True
    assert _shape(session) == [("q", "", "stopped")]


def test_a_stopped_request_drops_late_rows_even_after_its_boundary_closed() -> None:
    request_id, session = "req:test:stop-closed", "s-stop-closed"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session)
    assert flush_staged_conversation_events(request_id, request_end=True) == 0  # request over
    _late_draft(request_id, session)
    assert _shape(session) == [("q", "", "stopped")]


def test_a_second_stop_of_the_same_turn_writes_nothing_more() -> None:
    request_id, session = "req:test:stop-twice", "s-stop-twice"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session)
    _stop(request_id, session)
    assert len(_rows(session)) == 1


def test_a_turn_sealed_before_the_stop_keeps_its_row_and_gets_no_second_one() -> None:
    request_id, session = "req:test:stop-after-seal", "s-stop-after-seal"
    open_transcript_commit_boundary(request_id)
    _late_draft(request_id, session, "draft")
    assert persist_staged_conversation_event(request_id=request_id, committed_text="The sealed answer.") is True
    _stop(request_id, session)
    assert pm._transcript_request_stopped(request_id) is False
    assert _shape(session) == [("q", "The sealed answer.", "committed")]


def test_try_again_after_a_stop_is_its_own_turn_and_records_normally() -> None:
    session = "s-stop-retry"
    stopped, retry = "req:test:stop-retry-1", "req:test:stop-retry-2"
    open_transcript_commit_boundary(stopped)
    _stop(stopped, session)
    open_transcript_commit_boundary(retry)
    _late_draft(retry, session, "draft 2")
    assert persist_staged_conversation_event(request_id=retry, committed_text="The answer this time.") is True
    assert [(r["assistant"], r["commit_state"], r["request_id"]) for r in _rows(session)] == [
        ("", "stopped", stopped),
        ("The answer this time.", "committed", retry),
    ]


def test_without_a_request_id_nothing_is_written() -> None:
    assert pm._record_stopped_turn("", session_id="s-stop-none", user_input="q") == "unknown"
    assert _rows("s-stop-none") == []


def test_a_live_stop_is_never_evicted_and_the_ended_ones_stay_bounded() -> None:
    # Review probe: 513 later turn endings must not push out a stop whose turn is still running.
    request_id, session = "req:test:stop-evict", "s-stop-evict"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session)
    with pm._TRANSCRIPT_STAGE_LOCK:
        for index in range(pm._TRANSCRIPT_OUTCOMES_MAX + 50):
            pm._note_transcript_outcome_locked(f"req:test:other-{index}", pm._OUTCOME_RECORDED)
    assert len(pm._TRANSCRIPT_OUTCOMES) == pm._TRANSCRIPT_OUTCOMES_MAX
    _late_draft(request_id, session)
    assert _shape(session) == [("q", "", "stopped")]
    # The request-end flush (the turn is done) retires the id into the bounded record.
    flush_staged_conversation_events(request_id, request_end=True)
    assert request_id not in pm._LIVE_STOPPED_REQUESTS
    assert pm._transcript_request_stopped(request_id) is True


def test_a_stopped_row_feeds_the_next_prompt_as_a_closed_exchange_not_as_a_reply() -> None:
    # Review probe: hydration from the session log.
    request_id, session = "req:test:stop-hydrate", "s-stop-hydrate"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session, "write me a poem")
    out = augment_history_from_session_log([{"role": "user", "content": "next question"}], session_id=session, user_text="next question")
    assert out == [
        {"role": "user", "content": "write me a poem"},
        {"role": "assistant", "content": CLOSED_EXCHANGE_NOTICE},
        {"role": "user", "content": "next question"},
    ]


def test_the_painted_marker_sent_back_by_the_page_is_a_closed_exchange_in_every_language() -> None:
    from core.bootstrap_context import _client_conversation_history
    from core.i18n.catalog import catalog_for

    german = catalog_for("de").text("run.stopped_showing")
    assert german != "run.stopped_showing" and german != MARKER
    for marker in (MARKER, german):
        client = [
            {"role": "user", "content": "write me a poem"},
            {"role": "assistant", "content": marker},
            {"role": "user", "content": "next question"},
        ]
        gated = augment_history_from_session_log(client, session_id="s-stop-client", user_text="next question")
        assert gated[1] == {"role": "assistant", "content": CLOSED_EXCHANGE_NOTICE}, gated
        prompt = _client_conversation_history(
            {"client_conversation_history": client}, current_user_text="next question", max_messages=10, max_chars=4000,
        )
        assert marker not in json.dumps(prompt, ensure_ascii=False), prompt
        assert {"role": "assistant", "content": CLOSED_EXCHANGE_NOTICE} in prompt, prompt


def test_a_stopped_row_is_not_mined_as_an_assistant_reply() -> None:
    from storage.dialogue_memory import recent_dialogue_turns

    request_id, session = "req:test:stop-mining", "s-stop-mining"
    open_transcript_commit_boundary(request_id)
    _stop(request_id, session, "tell me about the lighthouse")
    assistant_turns = recent_dialogue_turns(session, limit=10, speaker_roles=("assistant",))
    assert assistant_turns == []


def test_a_stream_that_ends_before_its_turn_holds_the_request_end_flush_until_the_turn_is_done() -> None:
    """Review case: Stop aborts the page's fetch before the cancel lands. The stream's request-end
    flush ran at once, wrote the worker's staged draft as ``unsealed`` and closed the turn, so the
    stop that followed could only say "already recorded"."""
    from core.web.api import runtime as runtime_mod
    from core.web.api import service as service_mod

    request_id, session = "req:test:abort-first", "s-abort-first"
    open_transcript_commit_boundary(request_id)
    with runtime_mod._STOP_RECORDERS_LOCK:
        runtime_mod._LIVE_STREAM_REQUESTS[request_id] = []  # the turn's worker is still running
    _late_draft(request_id, session)  # the worker staged its draft
    stream = service_mod._flush_staged_transcript_after(request_id, iter([b"{}\n"]))
    next(stream)
    stream.close()  # the page's fetch is gone
    assert _rows(session) == [] and has_staged_conversation_event(request_id) is True
    _stop(request_id, session)  # the cancel lands
    runtime_mod._turn_ended(request_id)  # the worker is done: the held flush runs now
    assert _shape(session) == [("q", "", "stopped")]
    assert request_id not in pm._LIVE_STOPPED_REQUESTS and pm._transcript_request_stopped(request_id)


def test_a_stream_closed_after_its_turn_ended_flushes_at_once_as_before() -> None:
    from core.web.api import service as service_mod

    request_id, session = "req:test:flush-now", "s-flush-now"
    open_transcript_commit_boundary(request_id)
    _late_draft(request_id, session, "lane text nobody sealed")
    stream = service_mod._flush_staged_transcript_after(request_id, iter([b"{}\n"]))
    list(stream)
    assert _shape(session) == [("q", "lane text nobody sealed", "unsealed")]


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


def _served_rows(daemon, session: str) -> list[dict]:
    path = daemon.home / "data" / "conversation_log.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    return [row for row in rows if row.get("session_id") == session]


def _start_turn(daemon, provider, session: str, turn_id: str, *, read_to_end: bool):
    body = {"messages": [{"role": "user", "content": STORY}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": turn_id, "model": daemon.model, "mode": "ask"}
    seen: list[dict] = []
    calls_before = len(provider.calls)
    stop_reading = threading.Event()

    def _read() -> None:
        request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        response = urlopen(request, timeout=300)
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    seen.append(json.loads(line))
                if not read_to_end and stop_reading.is_set():
                    return  # the page aborts its fetch
        finally:
            response.close()

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    deadline = time.time() + 120
    while time.time() < deadline and not any(STORY in c["text"] for c in provider.calls[calls_before:]):
        time.sleep(0.1)
    assert any(STORY in c["text"] for c in provider.calls[calls_before:]), f"the turn never reached the model: {seen[-3:]}"
    return reader, stop_reading, seen


def _finish_late(held, settle_s: float) -> None:
    held.release.set()
    time.sleep(settle_s)
    held.release.clear()


def _export(daemon, session: str) -> str:
    with urlopen(f"{daemon.base_url}/api/chat/export?format=md&session={session}", timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def test_served_stop_is_in_history_at_once_and_the_late_finish_never_adds_a_draft(served) -> None:
    daemon, provider, held = served
    session = rig.canonical_session("stopped-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    reader, stop_reading, _seen = _start_turn(daemon, provider, session, turn_id, read_to_end=False)
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled"
    stop_reading.set()
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    _finish_late(held, 6.0)
    reader.join(timeout=30)
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    exported = _export(daemon, session)
    assert STORY in exported and MARKER in exported
    assert "LATE DRAFT" not in exported and "couldn't complete my check" not in exported
    assert [(r["assistant"], r.get("commit_state")) for r in _served_rows(daemon, session)] == [("", "stopped")]


def test_served_stop_that_aborts_the_stream_before_the_cancel_lands_records_the_stop(served) -> None:
    daemon, provider, held = served
    session = rig.canonical_session("stopped-abort-first-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    reader, stop_reading, _seen = _start_turn(daemon, provider, session, turn_id, read_to_end=False)
    stop_reading.set()  # the page's fetch is gone first ...
    reader.join(timeout=30)
    time.sleep(1.0)
    # ... and the cancel lands after it: the turn is still running and is recorded as stopped.
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled"
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    _finish_late(held, 6.0)
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    assert [(r["assistant"], r.get("commit_state")) for r in _served_rows(daemon, session)] == [("", "stopped")]


def test_served_stop_with_the_client_still_reading_keeps_the_typed_cancel_and_one_row(served) -> None:
    daemon, provider, held = served
    session = rig.canonical_session("stopped-reading-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    reader, _stop_reading, seen = _start_turn(daemon, provider, session, turn_id, read_to_end=True)
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled"
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    _finish_late(held, 1.0)
    reader.join(timeout=120)
    assert not reader.is_alive(), "the stream never ended after the cancel"
    typed = [str(obj["vool_event"].get("type")) for obj in seen if obj.get("vool_event")]
    assert "task.cancelled" in typed, typed
    published = "".join(str((obj.get("message") or {}).get("content") or "") for obj in seen)
    assert "LATE DRAFT" not in published
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    assert [(r["assistant"], r.get("commit_state")) for r in _served_rows(daemon, session)] == [("", "stopped")]
    # The worker released its registration: the stop is over, a repeat finds nothing to stop.
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "not_found"
