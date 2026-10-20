"""A stop and the answer's seal decide the turn once: whichever counts first is the turn's record.

Review of 9bc2382: ``finalize_answer`` read the turn's cancel marker and only then bound the
answer. The cancel door records the person's stop before it sets that marker, so a stop landing
between the two bound the answer nobody saw as plaintext in ``a7_finalizations`` and handed it on
as the turn's reply (memory extraction, the turn-context page that feeds later prompts), while the
transcript said "stopped".

Now the seal claims the turn under the transcript's own lock before the answer is bound:

* a stop recorded first wins: the seal commits the typed cancel notice, never the answer;
* a stop that lands while the seal holds its claim waits for it: a bound answer is the turn's
  record (the door answers "committed" and the page keeps showing it), and a bind that fails
  records the stop, once, with no draft left behind.

What a turn learns from its exchange follows the same record. A served stream seals its answer
after the turn's worker returned, and the worker reached memory extraction and the turn-context
page before that, so both are held until the transcript records the answer and are dropped when
the person stopped the turn. A normal stop (the turn ends with the typed cancel notice) feeds
neither.

Each ordering is forced here with hooks that wait on the state they need, never with sleeps (the
served cases through ``tests._stop_seal_gate``).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from urllib.request import Request, urlopen

import pytest

import core.finalization as fin
import core.persistent_memory as pm
from core.context_history_authority import CLOSED_EXCHANGE_NOTICE
from core.final_response_store import FinalizationRejected
from core.memory.files import conversation_log_path, load_jsonl
from core.persistent_memory import (
    TRANSCRIPT_COMMIT_BOUNDARY_KEY,
    append_conversation_event,
    augment_history_from_session_log,
    flush_staged_conversation_events,
    has_staged_conversation_event,
    open_transcript_commit_boundary,
)
from core.semantic.semantic_admissions import bound_request_context

SERVED = {"surface": "web", TRANSCRIPT_COMMIT_BOUNDARY_KEY: True}
QUESTION = "tell me a short story about a harbour cat"
ANSWER = "A harbour cat named Pip slept on the warm nets all winter (UNSEEN ANSWER)."


def _rows(session_id: str) -> list[dict]:
    path = conversation_log_path()
    rows = load_jsonl(path) if path.exists() else []
    return [row for row in rows if isinstance(row, dict) and row.get("session_id") == session_id]


def _shape(session_id: str) -> list[tuple[str, str, str | None]]:
    return [(r["user"], r["assistant"], r.get("commit_state")) for r in _rows(session_id)]


def _bound_content(finalization_id: str) -> str | None:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT canonical_content FROM a7_finalizations WHERE finalization_id = ?", (finalization_id,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else str(row[0] or "")


def _plaintext_anywhere(needle: str) -> bool:
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute("SELECT canonical_content FROM a7_finalizations").fetchall()
    finally:
        conn.close()
    return any(needle in str(row[0] or "") for row in rows)


class _Door:
    """What the cancel door does when the person presses Stop: record the stop, then set the
    turn's cancel marker. Run on its own thread, as the door runs on the server's."""

    def __init__(self, request_id: str, session_id: str, cancel: threading.Event) -> None:
        self.request_id, self.session_id, self.cancel = request_id, session_id, cancel
        self.outcome: str | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.outcome = append_conversation_event(
            session_id=self.session_id, user_input=QUESTION, assistant_output="", source_context=SERVED,
            stopped_request_id=self.request_id,
        )
        if self.outcome != "already_recorded":
            self.cancel.set()

    def press(self) -> _Door:
        self.thread.start()
        return self


def _until(predicate, what: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"never reached: {what}")
        time.sleep(0.005)


class _CountingEvent(threading.Event):
    """The seal's Event, counting the stops that wait on it (so a test knows they are waiting)."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = 0

    def wait(self, timeout=None):
        self.waiting += 1
        return super().wait(timeout)


def _watch_the_seal(request_id: str) -> _CountingEvent:
    with pm._TRANSCRIPT_STAGE_LOCK:
        assert request_id in pm._SEALS_IN_FLIGHT, "the seal holds no claim"
        watched = pm._SEALS_IN_FLIGHT[request_id] = _CountingEvent()
    return watched


def _turn(request_id: str, session_id: str, cancel: threading.Event) -> dict:
    """One served turn: the lane stages its row, then the seal runs (as the router finalizes)."""
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id=session_id, user_input=QUESTION, assistant_output=ANSWER)
        reset_admission()
        turn_id = "t-" + request_id
        admit_semantic_result({"response": ANSWER, "route_reason": "model_lane"}, turn_id=turn_id)
        return fin.finalize_answer(turn_id=turn_id, canonical_content=ANSWER, source_context={"cancel_event": cancel})


def test_a_stop_between_the_cancel_check_and_the_seal_commits_the_notice_never_the_unseen_answer(monkeypatch) -> None:
    request_id, session = "req:test:seal-order-1", "s-seal-order-1"
    cancel = threading.Event()
    real_marker = fin._turn_cancel_marker_fired
    doors: list[_Door] = []

    def marker_read_then_stop(context):
        fired = real_marker(context)
        if context and context.get("cancel_event") is cancel and not doors:
            # The marker was read (not set yet); the person's stop lands right now, before the seal.
            doors.append(_Door(request_id, session, cancel).press())
            doors[0].thread.join(10)
        return fired

    monkeypatch.setattr(fin, "_turn_cancel_marker_fired", marker_read_then_stop)
    commit = _turn(request_id, session, cancel)
    assert doors, "the stop never landed between the check and the seal"
    # No unseen plaintext: neither the commit, the bound row nor any row of the store holds it.
    assert commit["canonical_content"] == fin.CANCELLED_BEFORE_PUBLICATION
    assert _bound_content(commit["finalization_id"]) == fin.CANCELLED_BEFORE_PUBLICATION
    assert not _plaintext_anywhere("UNSEEN ANSWER")
    assert doors[0].outcome == "stopped"
    assert _shape(session) == [(QUESTION, "", "stopped")]
    assert pm._transcript_request_stopped(request_id) is True
    assert flush_staged_conversation_events(request_id, request_end=True) == 0
    assert "UNSEEN ANSWER" not in conversation_log_path().read_text(encoding="utf-8")


def _hold_the_bind(monkeypatch, request_id: str, session: str, cancel: threading.Event, *, fail: bool) -> list[_Door]:
    """Press Stop while the seal holds its claim: the door must be waiting for the seal's verdict
    before the bind goes on (or fails)."""
    real_bind = fin._bind_durably
    doors: list[_Door] = []

    def bind(commit, **kwargs):
        seal = _watch_the_seal(request_id)
        doors.append(_Door(request_id, session, cancel).press())
        _until(lambda: seal.waiting == 1, "the stop waits for the seal")
        assert request_id in pm._STOPS_PENDING_SEAL and doors[0].outcome is None
        if fail:
            raise FinalizationRejected("t-" + request_id, "sha256:stored", "sha256:attempted")
        return real_bind(commit, **kwargs)

    monkeypatch.setattr(fin, "_bind_durably", bind)
    return doors


def test_a_stop_during_a_seal_that_binds_leaves_the_committed_answer_as_the_record(monkeypatch) -> None:
    request_id, session = "req:test:seal-order-2", "s-seal-order-2"
    cancel = threading.Event()
    doors = _hold_the_bind(monkeypatch, request_id, session, cancel, fail=False)
    commit = _turn(request_id, session, cancel)
    doors[0].thread.join(10)
    # The door tells the page "committed": the answer it is showing is the turn's record.
    assert doors[0].outcome == "already_recorded"
    assert not cancel.is_set()
    assert commit["canonical_content"] == ANSWER
    assert _shape(session) == [(QUESTION, ANSWER, "committed")]
    # Not stopped: memory and the next prompt take this exchange as answered.
    assert pm._transcript_request_stopped(request_id) is False
    assert request_id not in pm._SEALS_IN_FLIGHT and request_id not in pm._STOPS_PENDING_SEAL


def test_a_stop_during_a_seal_whose_bind_fails_records_one_stopped_row_and_no_draft(monkeypatch) -> None:
    request_id, session = "req:test:seal-order-3", "s-seal-order-3"
    cancel = threading.Event()
    doors = _hold_the_bind(monkeypatch, request_id, session, cancel, fail=True)
    with pytest.raises(FinalizationRejected):
        _turn(request_id, session, cancel)
    doors[0].thread.join(10)
    assert doors[0].outcome == "stopped" and cancel.is_set()
    assert _shape(session) == [(QUESTION, "", "stopped")]
    assert has_staged_conversation_event(request_id) is False
    assert flush_staged_conversation_events(request_id, request_end=True) == 0
    assert _shape(session) == [(QUESTION, "", "stopped")]
    assert "UNSEEN ANSWER" not in conversation_log_path().read_text(encoding="utf-8")
    assert request_id not in pm._SEALS_IN_FLIGHT and request_id not in pm._STOPS_PENDING_SEAL
    # A restart in between: nothing in memory survives, the stored row alone is read back.
    with pm._TRANSCRIPT_STAGE_LOCK:
        pm._TRANSCRIPT_OUTCOMES.clear()
        pm._LIVE_STOPPED_REQUESTS.clear()
        pm._STAGED_TRANSCRIPT_EVENTS.clear()
        pm._OPEN_TRANSCRIPT_BOUNDARIES.clear()
    hydrated = augment_history_from_session_log([{"role": "user", "content": "next question"}], session_id=session, user_text="next question")
    assert hydrated == [
        {"role": "user", "content": QUESTION},
        {"role": "assistant", "content": CLOSED_EXCHANGE_NOTICE},
        {"role": "user", "content": "next question"},
    ]


def test_two_stops_during_one_seal_wait_for_it_and_write_one_row(monkeypatch) -> None:
    request_id, session = "req:test:seal-order-twice", "s-seal-order-twice"
    cancel = threading.Event()
    doors: list[_Door] = []

    def bind(commit, **kwargs):
        seal = _watch_the_seal(request_id)
        doors.append(_Door(request_id, session, cancel).press())
        doors.append(_Door(request_id, session, cancel).press())
        _until(lambda: seal.waiting == 2, "both stops wait for the seal")
        raise FinalizationRejected("t-" + request_id, "sha256:stored", "sha256:attempted")

    monkeypatch.setattr(fin, "_bind_durably", bind)
    with pytest.raises(FinalizationRejected):
        _turn(request_id, session, cancel)
    for door in doors:
        door.thread.join(10)
    assert [d.outcome for d in doors] == ["stopped", "stopped"]
    assert _shape(session) == [(QUESTION, "", "stopped")]


def test_a_stop_after_the_seal_committed_finds_the_answer_recorded() -> None:
    request_id, session = "req:test:seal-order-after", "s-seal-order-after"
    cancel = threading.Event()
    commit = _turn(request_id, session, cancel)
    door = _Door(request_id, session, cancel).press()
    door.thread.join(10)
    assert door.outcome == "already_recorded" and not cancel.is_set()
    assert commit["canonical_content"] == ANSWER
    assert _shape(session) == [(QUESTION, ANSWER, "committed")]


def test_the_door_recorder_reports_what_the_stop_decided() -> None:
    """``record_stopped_turn`` is what /api/chat/cancel calls; "already_recorded" is its "committed"."""
    from core.web.api import runtime as runtime_mod

    session = "s-seal-door"
    stopped, sealed = "req:test:seal-door-1", "req:test:seal-door-2"
    for request_id in (stopped, sealed):
        open_transcript_commit_boundary(request_id)
    with bound_request_context(stopped):
        stop_stopped = runtime_mod._stopped_turn_recorder(session, QUESTION, dict(SERVED))
    with bound_request_context(sealed):
        stop_sealed = runtime_mod._stopped_turn_recorder(session, QUESTION, dict(SERVED))
        append_conversation_event(source_context=SERVED, session_id=session, user_input=QUESTION, assistant_output=ANSWER)
    assert pm.persist_staged_conversation_event(request_id=sealed, committed_text=ANSWER) is True
    assert stop_stopped() == "stopped"
    assert stop_stopped() == "already_stopped"
    assert stop_sealed() == "already_recorded"
    assert [(r["assistant"], r.get("commit_state"), r["request_id"]) for r in _rows(session)] == [
        (ANSWER, "committed", sealed),
        ("", "stopped", stopped),
    ]


# ---------------------------------------------------------------------------------------------
# The served workflow: real daemon, real /api/chat stream, real /api/chat/cancel, real memory
# extraction (it posts to the scripted model) and the real turn-context page store.
# ---------------------------------------------------------------------------------------------

STORY = "tell me a very long story about a lighthouse keeper"
HELD_DRAFT = "Once upon a time a keeper named Ada (HELD DRAFT)"
NEXT = "my favourite colour is teal, remember that"
MARKER = "(stopped showing this answer)"


class _Model:
    """STORY is held until released, then answered; QUESTION is answered at once (UNSEEN)."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def __call__(self, body: dict) -> str | None:
        from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
        from core.fact_extractor import FACT_EXTRACT_SYSTEM

        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        system = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        if FACT_EXTRACT_SYSTEM[:60] in system:
            return None
        if AMBIGUITY_SYSTEM_PROMPT[:60] in system:
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        last_user = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        if STORY in last_user:
            self.release.wait(60)
            return HELD_DRAFT
        if QUESTION in last_user:
            return ANSWER
        return None


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    import tests._reader_served_rig as rig
    from tests._stop_seal_gate import start_gated

    model = _Model()
    root = tmp_path_factory.mktemp("stop-seal")
    with rig.CapturingProvider(default="Pasta with lemon and peas.", reply_fn=model) as provider:
        # Memory extraction posts to the raw Ollama API: point it at the scripted model too, so
        # what it is handed is observed (the session fence otherwise points it at a dead port).
        daemon = rig.ServedDaemon(root / "home", provider=provider, env_extra={"VOOL_RAW_OLLAMA_API_URL": provider.base_url})
        start_gated(daemon, root / "gate")
        assert daemon.certify(timeout=180).get("state") == "verified"
        try:
            yield daemon, provider, model, root / "gate"
        finally:
            model.release.set()
            daemon.stop()


def _post(daemon, path: str, body: dict) -> dict:
    request = Request(f"{daemon.base_url}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def _stream(daemon, session: str, turn_id: str, text: str, seen: list[dict]) -> threading.Thread:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": turn_id, "model": daemon.model, "mode": "ask"}

    def _read() -> None:
        request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=300) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    seen.append(json.loads(line))

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    return reader


def _history(daemon, session: str) -> list[tuple[str, str]]:
    with urlopen(f"{daemon.base_url}/api/chat/history?session={session}", timeout=60) as response:
        payload = json.loads(response.read().decode())
    return [(m.get("role"), m.get("content")) for m in payload.get("messages", [])]


def _db_texts(daemon, sql: str, *args) -> list[str]:
    con = sqlite3.connect(daemon.home / "data" / "vool_web0_v2.db")
    try:
        return [str(row[0] or "") for row in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _turn_context_admissions(daemon, session: str) -> list[str]:
    # The store creates its table with the first page it admits.
    if not _db_texts(daemon, "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'turn_context_admissions'"):
        return []
    return _db_texts(daemon, "SELECT admission_id FROM turn_context_admissions WHERE session_id = ?", session)


def _extraction_calls(provider) -> list[str]:
    from core.fact_extractor import FACT_EXTRACT_SYSTEM

    return [c["text"] for c in list(provider.calls) if FACT_EXTRACT_SYSTEM[:60] in c["text"]]


def _next_turn(daemon, provider, session: str) -> tuple[list[str], list[str]]:
    """Ask the next question in the same chat. Returns (that turn's prompts, every extraction call
    so far), once that turn's own extraction arrived: the control that extraction is observed."""
    calls_before = len(provider.calls)
    seen: list[dict] = []
    _stream(daemon, session, uuid.uuid4().hex, NEXT, seen).join(timeout=180)
    _until(
        lambda: any(NEXT in text for text in _extraction_calls(provider)),
        "the next turn's own extraction; calls: " + repr([(c["path"], c["text"][:90]) for c in list(provider.calls)[calls_before:]]),
        timeout=60,
    )
    prompts = [c["text"] for c in list(provider.calls)[calls_before:]]
    return prompts, _extraction_calls(provider)


def test_served_a_normal_stop_admits_no_cancel_notice_as_an_answer(served) -> None:
    import tests._reader_served_rig as rig

    daemon, provider, model, _gate = served
    session = rig.canonical_session("stop-notice-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    calls_before = len(provider.calls)
    seen: list[dict] = []
    reader = _stream(daemon, session, turn_id, STORY, seen)
    _until(lambda: any(STORY in c["text"] for c in list(provider.calls)[calls_before:]), "the turn reaches the model", timeout=120)
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled"
    model.release.set()
    reader.join(timeout=120)
    model.release.clear()
    assert not reader.is_alive()
    notice = fin.CANCELLED_BEFORE_PUBLICATION
    assert notice in json.dumps(seen, ensure_ascii=False)  # what this turn ended with
    assert _history(daemon, session) == [("user", STORY), ("assistant", MARKER)]
    # No page of the stopped turn feeds later prompts, nothing of it is mined as the assistant's
    # reply, and the next turn never sees the notice or the draft.
    assert _turn_context_admissions(daemon, session) == []
    prompts, extractions = _next_turn(daemon, provider, session)
    assert [t for t in extractions if notice[:40] in t or "HELD DRAFT" in t] == []
    assert [t for t in prompts if notice[:40] in t or "HELD DRAFT" in t] == []
    assert len(_turn_context_admissions(daemon, session)) == 1  # the next turn's own page: admission works


def _hold(gate, hold: str, reached: str) -> None:
    for name in ("go", reached):
        (gate / name).unlink(missing_ok=True)
    (gate / hold).touch()


def test_served_a_stop_after_the_worker_learned_and_before_the_seal_leaves_the_unseen_answer_nowhere(served) -> None:
    """The served stream seals its answer after the turn's worker returned, and the worker decided
    what it learns from the exchange before that. A stop landing in between used to leave the
    answer nobody saw in memory and in the turn-context page that feeds later prompts."""
    import tests._reader_served_rig as rig
    from tests._stop_seal_gate import wait_for

    daemon, provider, _model, gate = served
    _hold(gate, "hold_after_learning", "after_learning")
    session = rig.canonical_session("stop-learned-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    seen: list[dict] = []
    reader = _stream(daemon, session, turn_id, QUESTION, seen)
    assert wait_for(gate / "after_learning"), daemon.log_tail()
    held_in = (gate / "after_learning").read_text(encoding="utf-8")
    # The model answered and the worker is about to return; the person's stop lands now.
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled", held_in
    (gate / "go").touch()
    reader.join(timeout=120)
    assert not reader.is_alive()
    # What the answer bubble paints (the streamed text and the commit) never holds the answer.
    painted = "".join(str((o.get("message") or {}).get("content") or "") for o in seen) + "".join(
        str(o["vool_response_commit"].get("canonical_content") or "") for o in seen if isinstance(o.get("vool_response_commit"), dict)
    )
    assert "UNSEEN ANSWER" not in painted and fin.CANCELLED_BEFORE_PUBLICATION in painted
    assert _history(daemon, session) == [("user", QUESTION), ("assistant", MARKER)]
    assert [t for t in _db_texts(daemon, "SELECT canonical_content FROM a7_finalizations") if "UNSEEN ANSWER" in t] == []
    assert _turn_context_admissions(daemon, session) == []
    prompts, extractions = _next_turn(daemon, provider, session)
    assert [t for t in extractions if "UNSEEN ANSWER" in t] == []
    assert [t for t in prompts if "UNSEEN ANSWER" in t] == []


def test_served_a_stop_after_the_turn_finished_running_finds_its_answer_as_the_record(served) -> None:
    """Between the worker's return and the stream's seal there is no running turn to stop: the
    door says so ("not_found", and the page keeps showing the answer that is on its way), the
    answer is committed, and memory takes it like the transcript does."""
    import tests._reader_served_rig as rig
    from tests._stop_seal_gate import wait_for

    daemon, provider, _model, gate = served
    _hold(gate, "hold_at_check", "at_check")
    session = rig.canonical_session("stop-finished-" + uuid.uuid4().hex[:8])
    turn_id = uuid.uuid4().hex
    seen: list[dict] = []
    reader = _stream(daemon, session, turn_id, QUESTION, seen)
    assert wait_for(gate / "at_check"), daemon.log_tail()
    assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "not_found"
    (gate / "go").touch()
    reader.join(timeout=120)
    commits = [o["vool_response_commit"].get("canonical_content") for o in seen if isinstance(o.get("vool_response_commit"), dict)]
    assert ANSWER in commits, (commits, [o["vool_event"].get("type") for o in seen if o.get("vool_event")], _history(daemon, session), daemon.log_tail(30))
    assert _history(daemon, session) == [("user", QUESTION), ("assistant", ANSWER)]
    _until(lambda: any("UNSEEN ANSWER" in t for t in _extraction_calls(provider)), "the answer's own extraction", timeout=60)
    assert len(_turn_context_admissions(daemon, session)) == 1
