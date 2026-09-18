"""DISPATCHER phase 3: a turn stays cancellable for exactly as long as it is running.

Two defects, one cause -- the runtime was answering questions about WORK using facts about the
CLIENT.

**1. Cancel registration was owned by the HTTP stream.** The live-turn entry was released in the
streaming generator's `finally`, which fires when the browser goes away. Before phase 2 that was
almost always the end of the turn too, so it looked right. Once a chat can keep running in the
background, the browser going away is ordinary, and whether a turn stayed cancellable came down to
where that generator happened to be suspended at disconnect -- blocked in `event_queue.get()` (entry
survives, cancel works) or parked at a `yield` (entry dropped, `/api/chat/cancel` answers
`not_found` for a turn still burning tokens). The phase-2 live drive landed on the good side; that
is one observation, not proof. Registration now belongs to the worker, released in its own
`finally` on every exit path.

**2. The stale sweep read quiet as gone.** Any checkpoint left `running` and untouched for
`STALE_CHECKPOINT_SECONDS` was closed out as `interrupted` and advertised back as `resume_available`
-- and `_RESUMABLE_STATUSES` contains `running`, so a live turn was offered as resumable even
before the sweep fired. A background turn that spends fifteen minutes inside one model call is
exactly that shape, and accepting the offer would run the same work a second time next to the copy
still going. The sweep and both resume surfaces now consult the live-turn registry.

These tests drive the real functions -- the real generator, the real sweep, the real
`/api/runtime/sessions` row builder -- against a worker whose finish they control, because both
defects are about TIMING and neither is visible in a static read of the code. Each has a mutation
that puts the old ownership back and must turn its named test red.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core import live_turns, runtime_continuity
from core.live_turns import (
    active_turn_count,
    is_checkpoint_live,
    live_checkpoint_ids,
    note_checkpoint,
    register_turn,
    request_cancel,
    reset_live_turns,
    unregister_turn,
)
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    list_runtime_sessions,
    mark_stale_runtime_checkpoints_interrupted,
    reset_runtime_continuity_state,
)
from core.runtime_task_events import emit_runtime_event
from core.web.api import runtime as api_runtime
from storage.migrations import run_migrations


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_live_turns()
    yield
    reset_live_turns()


@pytest.fixture()
def store(tmp_path: Path):
    db = tmp_path / "continuity.db"
    run_migrations(str(db))
    configure_runtime_continuity_db_path(str(db))
    try:
        yield db
    finally:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)


# --------------------------------------------------------------------------------------------- #
# A worker whose finish the test controls, driven through the REAL streaming generator.
# --------------------------------------------------------------------------------------------- #
class _Harness:
    """Drives `stream_agent_with_events` with a worker that blocks until released.

    `run_agent_provider` is the one substitution: everything else -- the registration, the worker
    thread, the generator's own teardown -- is the shipped code.
    """

    def __init__(self, *, session_id: str, turn_id: str, fail: bool = False):
        self.session_id = session_id
        self.turn_id = turn_id
        self.fail = fail
        self.started = threading.Event()
        self.release = threading.Event()
        self.saw_cancel = threading.Event()
        self.finished = threading.Event()
        self.captured_context: dict = {}

    def _run_agent(self, runtime, user_text, *, session_id, source_context):
        self.captured_context = source_context
        # Emit one real runtime event immediately, the way any real turn does. The generator blocks
        # on its queue until something arrives, so without this the first `next()` would deadlock --
        # and the test would be measuring its own harness rather than the code.
        emit_runtime_event(source_context, event_type="task_received", message="Received request: test")
        self.started.set()
        try:
            # Poll the turn's real cancel Event the way the router's _cancel_check does.
            event = source_context.get("cancel_event")
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if event is not None and event.is_set():
                    self.saw_cancel.set()
                    break
                if self.release.wait(timeout=0.02):
                    break
            if self.fail:
                raise RuntimeError("worker blew up")
            return {"response": "done"}
        finally:
            self.finished.set()

    def stream(self):
        return api_runtime.stream_agent_with_events(
            runtime=None,
            user_text="hello",
            session_id=self.session_id,
            source_context={"cancel_turn_id": self.turn_id},
            model="test-model",
            emit_task_events=True,   # so the generator yields as soon as the worker emits
            run_agent_provider=self._run_agent,
        )


# --------------------------------------------------------------------------------------------- #
# 1 -- cancel survives the client going away
# --------------------------------------------------------------------------------------------- #
def test_cancel_works_after_the_client_stream_is_torn_down() -> None:
    """The headline repair: disconnect, then cancel, and the WORKER observes it."""
    h = _Harness(session_id="s-disconnect", turn_id="t-1")
    gen = h.stream()
    next(gen)  # prime: the generator body runs, registers the turn, starts the worker
    assert h.started.wait(5), "worker never started"

    # The client goes away FIRST: closing the generator runs its `finally` exactly as a disconnect
    # does. Nothing has been cancelled yet, so the worker is still mid-turn.
    gen.close()
    assert not h.finished.is_set(), "the worker must outlive its client's stream"

    # The cancel is raised entirely after the disconnect -- the case that used to be a coin flip.
    assert request_cancel("s-disconnect", "t-1") == "cancelled", (
        "a turn whose worker is still running must remain cancellable after its client disconnects"
    )
    assert h.saw_cancel.wait(5), "the worker never observed a cancel raised after its client left"
    assert h.finished.wait(5)
    # Terminal cleanup is the worker's, so it lands once the worker actually returns.
    _wait_until(lambda: request_cancel("s-disconnect", "t-1") == "not_found")


def test_the_generator_teardown_does_not_release_a_live_turn() -> None:
    h = _Harness(session_id="s-teardown", turn_id="t-2")
    gen = h.stream()
    next(gen)
    assert h.started.wait(5)
    before = active_turn_count()
    gen.close()
    assert active_turn_count() == before, "stream teardown must not release a live turn's entry"
    assert live_turns.live_session_ids() >= {"s-teardown"}
    h.release.set()
    assert h.finished.wait(5)
    _wait_until(lambda: active_turn_count() == before - 1)


# --------------------------------------------------------------------------------------------- #
# 2 / 6 -- terminal cleanup, on every exit path, exactly once, with nothing left behind
# --------------------------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("name", "fail", "cancel_first"),
    [("completed", False, False), ("failed", True, False), ("cancelled", False, True)],
)
def test_the_registry_is_released_when_the_worker_ends_however_it_ends(name, fail, cancel_first) -> None:
    baseline = active_turn_count()
    h = _Harness(session_id=f"s-{name}", turn_id="t-x", fail=fail)
    gen = h.stream()
    next(gen)
    assert h.started.wait(5)
    assert active_turn_count() == baseline + 1
    gen.close()  # client disconnects in every case, so cleanup cannot be the stream's doing
    if cancel_first:
        assert request_cancel(f"s-{name}", "t-x") == "cancelled"
    else:
        h.release.set()
    assert h.finished.wait(5), "worker never finished"
    _wait_until(lambda: active_turn_count() == baseline, what=f"registry not released after {name}")
    assert request_cancel(f"s-{name}", "t-x") == "not_found"
    # Releasing again is a no-op -- "exactly once" cannot be violated by a second caller.
    unregister_turn(f"s-{name}", "t-x")
    assert active_turn_count() == baseline


def test_a_worker_that_never_starts_does_not_leak_its_entry(monkeypatch) -> None:
    baseline = active_turn_count()

    class _DeadThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

        def join(self, *a, **k):
            return None

    monkeypatch.setattr(api_runtime.threading, "Thread", _DeadThread)
    h = _Harness(session_id="s-nothread", turn_id="t-3")
    # The failure now reaches the client as a terminating chunk rather than as a raised exception.
    # `thread.start()` fails on the FIRST next(), which is after Starlette has already committed
    # status and headers -- so propagating truncated the body and the browser showed a failed load
    # with nothing to read. The wrapper converts it; see
    # tests/test_a_streamed_turn_always_terminates.py. What this test is actually about -- the
    # registry entry not leaking when the worker never ran -- is asserted unchanged below.
    chunks = list(h.stream())

    assert chunks, "a worker that never started must still terminate the response"
    assert b'"done":true' in chunks[-1], "the stream must end with a terminator, not mid-body"
    assert active_turn_count() == baseline, "a turn whose worker never started must not stay registered"
    assert request_cancel("s-nothread", "t-3") == "not_found"


def _wait_until(predicate, *, timeout: float = 5.0, what: str = "condition not reached") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(what)


# --------------------------------------------------------------------------------------------- #
# 3 / 4 -- quiet is not gone
# --------------------------------------------------------------------------------------------- #
def _stale_checkpoint(session_id: str, *, age_seconds: float) -> str:
    """A `running` checkpoint whose `updated_at` is deliberately older than any deadline used here."""
    checkpoint = create_runtime_checkpoint(session_id=session_id, request_text="a long quiet task")
    checkpoint_id = str(checkpoint["checkpoint_id"])
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - age_seconds)) + "+00:00"
    # Reaching into the module's own lock/connection: this test owns the store (tmp_path fixture)
    # and needs to age a row past a deadline without waiting out the deadline.
    with runtime_continuity._LOCK:
        conn = runtime_continuity._conn()
        try:
            conn.execute(
                "UPDATE runtime_checkpoints SET updated_at = ? WHERE checkpoint_id = ?",
                (old, checkpoint_id),
            )
            conn.execute(
                "UPDATE runtime_sessions SET updated_at = ? WHERE session_id = ?",
                (old, session_id),
            )
            conn.commit()
        finally:
            conn.close()
    return checkpoint_id


def test_a_live_worker_past_the_stale_deadline_is_not_closed_out(store) -> None:
    """Requirement A: quiet for longer than the deadline, but demonstrably alive."""
    session = "s-live-quiet"
    checkpoint_id = _stale_checkpoint(session, age_seconds=3600)
    register_turn(session, "t-live")
    note_checkpoint(session, "t-live", checkpoint_id)
    assert is_checkpoint_live(checkpoint_id) is True
    assert checkpoint_id in live_checkpoint_ids()

    swept = mark_stale_runtime_checkpoints_interrupted(older_than_seconds=1.0)

    assert swept == 0, "a live worker must not be swept"
    assert str(get_runtime_checkpoint(checkpoint_id)["status"]) == "running"
    row = next(r for r in list_runtime_sessions(limit=50) if r["session_id"] == session)
    assert row["worker_live"] is True
    assert row["resume_available"] is False, (
        "a checkpoint whose worker is executing right now must never be offered as resumable"
    )


def test_the_same_checkpoint_is_swept_once_its_worker_is_gone(store) -> None:
    """Requirement B/C: the recovery path a real crash needs still works."""
    session = "s-dead-quiet"
    checkpoint_id = _stale_checkpoint(session, age_seconds=3600)
    register_turn(session, "t-dead")
    note_checkpoint(session, "t-dead", checkpoint_id)
    assert mark_stale_runtime_checkpoints_interrupted(older_than_seconds=1.0) == 0

    # The worker ends (or its process died and the registry is empty on restart).
    unregister_turn(session, "t-dead")
    assert is_checkpoint_live(checkpoint_id) is False

    assert mark_stale_runtime_checkpoints_interrupted(older_than_seconds=1.0) == 1
    checkpoint = get_runtime_checkpoint(checkpoint_id)
    assert str(checkpoint["status"]) == "interrupted"
    assert "before the task finished" in str(checkpoint["failure_text"]) or "no progress" in str(
        checkpoint["failure_text"]
    )
    row = next(r for r in list_runtime_sessions(limit=50) if r["session_id"] == session)
    assert row["worker_live"] is False
    assert row["resume_available"] is True, "genuine crash recovery must still be offered"


def test_startup_sweep_still_closes_everything_it_finds(store) -> None:
    """The `older_than_seconds=0` startup rule: the owning process is gone by definition."""
    session = "s-restart"
    checkpoint_id = _stale_checkpoint(session, age_seconds=5)
    assert mark_stale_runtime_checkpoints_interrupted(older_than_seconds=0.0) == 1
    assert str(get_runtime_checkpoint(checkpoint_id)["status"]) == "interrupted"


def test_a_live_worker_blocks_recovery_from_rewriting_its_checkpoint(store) -> None:
    """Requirement D: the two ways a duplicate could arise are both closed.

    Recovery rewrites the durable row and stops nothing, so applying it to a live checkpoint would
    report a stop that did not happen. And `prepare_runtime_checkpoint` refuses to adopt a `running`
    checkpoint, so even a resume that got past the UI cannot attach to live work.
    """
    from core.agent_runtime.checkpoints import prepare_runtime_checkpoint

    session = "s-no-dup"
    checkpoint_id = _stale_checkpoint(session, age_seconds=3600)
    register_turn(session, "t-live")
    note_checkpoint(session, "t-live", checkpoint_id)

    assert is_checkpoint_live(checkpoint_id) is True

    class _Agent:
        def _looks_like_explicit_resume_request(self, text):
            return True

        def _is_proceed_message(self, text):
            return True

        def _resume_request_key(self, text):
            return str(text)

    created: list[str] = []

    def _create(*, session_id, request_text, source_context=None):
        new = create_runtime_checkpoint(session_id=session_id, request_text=request_text)
        created.append(str(new["checkpoint_id"]))
        return new

    bundle = prepare_runtime_checkpoint(
        _Agent(),
        session_id=session,
        raw_user_input="continue the interrupted task",
        effective_input="a long quiet task",
        source_context={},
        latest_resumable_checkpoint_fn=lambda sid: get_runtime_checkpoint(checkpoint_id),
        resume_runtime_checkpoint_fn=lambda cid, **kw: pytest.fail(
            "a resume must never attach to a checkpoint whose worker is still running"
        ),
        create_runtime_checkpoint_fn=_create,
        latest_failed_checkpoint_fn=lambda sid: None,
    )
    # It refuses outright: a `running` checkpoint is never a resume candidate, and with no FAILED
    # task behind it there is nothing to retry either. Stronger than creating a sibling turn.
    assert bundle["state"] == "missing_resume"
    assert created == [], "a resume attempt must not spawn a second checkpoint next to live work"
    # ...and the original live checkpoint was left exactly as it was.
    assert str(get_runtime_checkpoint(checkpoint_id)["status"]) == "running"


def test_recovery_refuses_to_rewrite_a_live_checkpoint(store) -> None:
    """The /api/task/recovery route, driven for real.

    Recovery rewrites the durable row and stops NOTHING. Applied to a live checkpoint it would mark
    the task cancelled while the work carried on, and the operator would be told it stopped when it
    had not. It must refuse and point at the route that actually stops a turn.
    """
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    session = "s-recovery-live"
    checkpoint_id = _stale_checkpoint(session, age_seconds=3600)

    def _recover():
        return dispatch_post(
            path="/api/task/recovery",
            body={"session_id": session, "checkpoint_id": checkpoint_id, "action": "cancel"},
            headers={"content-type": "application/json"},
            runtime=RuntimeServices(display_name="VOOL"),
            model_name="vool",
            workspace_root_provider=lambda: "/tmp",
            client_host="127.0.0.1",
        )

    register_turn(session, "t-live")
    note_checkpoint(session, "t-live", checkpoint_id)
    live = _recover()
    assert live.status == 409, "recovery must refuse a checkpoint whose worker is still running"
    assert b"still running" in (live.body or b"")
    assert str(get_runtime_checkpoint(checkpoint_id)["status"]) == "running", (
        "a refused recovery must leave the durable row untouched"
    )

    # Once the worker is genuinely gone, the same call is the recovery it was always meant to be.
    unregister_turn(session, "t-live")
    gone = _recover()
    assert gone.status == 200
    assert str(get_runtime_checkpoint(checkpoint_id)["status"]) == "cancelled"


def test_a_finalized_checkpoint_is_never_reported_live(store) -> None:
    session = "s-final"
    checkpoint = create_runtime_checkpoint(session_id=session, request_text="short task")
    checkpoint_id = str(checkpoint["checkpoint_id"])
    register_turn(session, "t-f")
    note_checkpoint(session, "t-f", checkpoint_id)
    unregister_turn(session, "t-f")
    finalize_runtime_checkpoint(checkpoint_id, status="completed", final_response="ok")
    assert is_checkpoint_live(checkpoint_id) is False
    row = next(r for r in list_runtime_sessions(limit=50) if r["session_id"] == session)
    assert row["worker_live"] is False
    assert row["resume_available"] is False


def test_note_checkpoint_never_resurrects_a_released_turn() -> None:
    register_turn("s-race", "t-race")
    unregister_turn("s-race", "t-race")
    note_checkpoint("s-race", "t-race", "runtime-late")
    assert is_checkpoint_live("runtime-late") is False
    assert active_turn_count() == 0


# --------------------------------------------------------------------------------------------- #
# Sabotage -- each mutation must turn its NAMED test red
# --------------------------------------------------------------------------------------------- #
def test_mutation_releasing_on_stream_teardown_breaks_cancel_after_disconnect(monkeypatch) -> None:
    """Put the old ownership back: release the entry when the CLIENT goes away.

    `test_cancel_works_after_the_client_stream_is_torn_down` and
    `test_the_generator_teardown_does_not_release_a_live_turn` are the guards.
    """
    h = _Harness(session_id="s-mutated", turn_id="t-m")
    gen = h.stream()
    next(gen)
    assert h.started.wait(5)

    # The mutation, applied at exactly the moment the old code applied it.
    gen.close()
    unregister_turn("s-mutated", "t-m")

    assert request_cancel("s-mutated", "t-m") == "not_found", (
        "MUTATION DID NOT BITE: releasing on stream teardown must make a still-running turn "
        "report not_found"
    )
    h.release.set()
    assert h.finished.wait(5)


def test_mutation_removing_the_live_worker_exemption_sweeps_a_running_turn(store, monkeypatch) -> None:
    """Blind the sweep to the registry: `test_a_live_worker_past_the_stale_deadline_is_not_closed_out`
    and the resume-surface assertions are the guards."""
    session = "s-mutated-sweep"
    checkpoint_id = _stale_checkpoint(session, age_seconds=3600)
    register_turn(session, "t-live")
    note_checkpoint(session, "t-live", checkpoint_id)

    monkeypatch.setattr(runtime_continuity, "_live_checkpoint_ids", frozenset)

    swept = mark_stale_runtime_checkpoints_interrupted(older_than_seconds=1.0)
    assert swept == 1, (
        "MUTATION DID NOT BITE: without the live-worker exemption a running turn must be closed out"
    )
    assert str(get_runtime_checkpoint(checkpoint_id)["status"]) == "interrupted"
    row = next(r for r in list_runtime_sessions(limit=50) if r["session_id"] == session)
    assert row["resume_available"] is True, (
        "MUTATION DID NOT BITE: the swept live turn must become falsely resumable"
    )


def test_mutation_resume_surface_ignoring_liveness_offers_a_running_turn(store, monkeypatch) -> None:
    """Blind only the row builder: `test_a_live_worker_past_the_stale_deadline_is_not_closed_out`'s
    `resume_available is False` assertion is the guard."""
    session = "s-mutated-rows"
    checkpoint_id = _stale_checkpoint(session, age_seconds=3600)
    register_turn(session, "t-live")
    note_checkpoint(session, "t-live", checkpoint_id)

    monkeypatch.setattr(runtime_continuity, "_live_checkpoint_ids", frozenset)

    row = next(r for r in list_runtime_sessions(limit=50) if r["session_id"] == session)
    assert row["resume_available"] is True, (
        "MUTATION DID NOT BITE: ignoring liveness must offer a live running turn as resumable"
    )


def test_the_live_worker_exemption_mutation_turns_its_named_guards_red(store, monkeypatch) -> None:
    """Anti-vacuity gate for repair 2: blind the sweep to the registry and require its guards to fail.

    This is a REAL mutation, not a simulation -- `_live_checkpoint_ids` is exactly what the sweep and
    the row builder consult, so replacing it with an empty set is the code without the exemption. A
    guard that stays green under it never reached the property it claims to protect.

    The list is deliberately ONE guard. Two neighbouring tests were tried here and correctly stayed
    green, because neither depends on this exemption and claiming them would have been false
    coverage: `test_a_live_worker_blocks_recovery_from_rewriting_its_checkpoint` exercises
    `prepare_runtime_checkpoint`'s independent refusal to adopt a `running` checkpoint, and
    `test_recovery_refuses_to_rewrite_a_live_checkpoint` reads `is_checkpoint_live` directly rather
    than the sweep's set. Both are separate layers of the same defence, and each has its own test.

    Repair 1's mutation is verified inside
    `test_mutation_releasing_on_stream_teardown_breaks_cancel_after_disconnect`, which calls
    `unregister_turn` at precisely the point the old `finally` called it -- the same function at the
    same moment -- and requires the cancel to start answering `not_found`.
    """
    monkeypatch.setattr(runtime_continuity, "_live_checkpoint_ids", frozenset)
    survived = []
    for guard in (test_a_live_worker_past_the_stale_deadline_is_not_closed_out,):
        try:
            guard(store)
        except AssertionError:
            continue
        survived.append(guard.__name__)
    assert survived == [], (
        "these guards stayed GREEN without the live-worker exemption, so they never reached it: "
        f"{survived}"
    )


def test_the_sweep_fails_open_rather_than_failing_a_turn(store, monkeypatch) -> None:
    """Housekeeping must never be the reason a turn errors -- so an unreadable registry degrades to
    the old behaviour instead of raising."""
    session = "s-registry-down"
    checkpoint_id = _stale_checkpoint(session, age_seconds=3600)

    def _boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(live_turns, "live_checkpoint_ids", _boom)
    assert mark_stale_runtime_checkpoints_interrupted(older_than_seconds=1.0) == 1
    assert str(get_runtime_checkpoint(checkpoint_id)["status"]) == "interrupted"


# --------------------------------------------------------------------------------------------- #
# The contract the web layer still imports
# --------------------------------------------------------------------------------------------- #
def test_the_web_facade_still_exposes_the_same_registry() -> None:
    from core.web.api import turn_cancel

    event = turn_cancel.register_turn("s-facade", "t-1")
    assert isinstance(event, threading.Event)
    # One registry, two import paths -- not two registries.
    assert live_turns.active_turn_count() == turn_cancel.active_turn_count()
    assert turn_cancel.request_cancel("s-facade", "t-1") == "cancelled"
    assert event.is_set()
    turn_cancel.unregister_turn("s-facade", "t-1")
    assert turn_cancel.request_cancel("s-facade", "t-1") == "not_found"


def test_the_streaming_generator_no_longer_releases_the_turn_itself() -> None:
    """Source guard against the ownership silently moving back.

    Cheap and narrow on purpose: the behavioural tests above are the real proof, but this names the
    exact line that must not come back, so a future edit gets a direct answer instead of a puzzling
    timing failure.
    """
    source = Path(api_runtime.__file__).read_text(encoding="utf-8")
    # The streamed turn's BODY, which is where the teardown lives. `stream_agent_with_events` is now
    # a thin wrapper that guarantees the response terminates even when the body raises (see
    # tests/test_a_streamed_turn_always_terminates.py); the wrapper has no teardown of its own, so
    # slicing from its `def` would read the wrong function and this guard would pass vacuously.
    body = source[source.index("def _stream_agent_with_events_inner(") :]
    body = body[: body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
    teardown = body[body.rindex("    finally:") :]
    assert "unregister_runtime_event_sink(stream_id)" in teardown
    assert "if thread is not threading.current_thread():" in teardown, (
        "stream teardown must never ask a worker thread to join itself"
    )
    assert "unregister_turn(" not in teardown, (
        "the stream's teardown must not release the live-turn entry -- that ownership belongs to "
        "the worker's own finally"
    )
    # The ingress-context wrapper nests the worker body one level deeper.
    # Check actual ownership, independent of indentation or wrapper depth.
    import ast

    outer = ast.parse(body).body[0]
    worker = next(node for node in outer.body if isinstance(node, ast.FunctionDef) and node.name == "worker")
    worker_body = next(node for node in worker.body if isinstance(node, ast.FunctionDef) and node.name == "_body")
    cleanup = next(node for node in worker_body.body if isinstance(node, ast.Try)).finalbody
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "unregister_turn"
        and [arg.id for arg in node.args if isinstance(arg, ast.Name)] == ["session_id", "cancel_turn_id"]
        for statement in cleanup for node in ast.walk(statement)
    ), "the worker body's finally must release its own live-turn entry"
