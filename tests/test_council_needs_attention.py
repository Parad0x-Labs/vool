"""C4 — a dead seat becomes the operator's decision, not the run's silent ending.

Bounded retries are exhausted and the seat is still dead. Until now that ended the run
`failed` — terminal, pin released, nothing to do but convene again from scratch and pay
for every seat's work a second time. The bench had done real work; a transport fault on
one seat threw all of it away.

C4 makes that a PAUSE the operator owns: NEEDS_ATTENTION, with retry / replace-model /
disable / resume, on the same run, the same ledger and the same chat card.

Worst cases, not greetings: a seat that dies in round THREE with two rounds of evidence
behind it, a disable that would leave no quorum, the same action sent twice, an action
from off the machine, and a stop racing a resume.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

CHAT = "openclaw:c4c4c4c4c4c4c4c4c4c4"

BENCH = [
    {"role_id": "builder", "model": "vendor/alpha", "votes": True},
    {"role_id": "falsifier", "model": "vendor/beta", "votes": True},
    {"role_id": "reviewer", "model": "vendor/gamma", "votes": True},
]


def _restore_pin_double(monkeypatch):
    from core.council import pin_lock

    def _restore(base_url, model, capability=""):
        assert capability, "restore_pin was called without the run's pin capability"
        assert pin_lock.is_dispatch_capability(capability, owner_local=True)

    monkeypatch.setattr("core.council.api.restore_pin", _restore)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from core.council import pin_lock

    pin_lock.reset_on_startup()

    def _patched(*parts):
        out = tmp_path / "council-data"
        for part in parts:
            out = out / part
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    monkeypatch.setattr("core.council.api.read_current_pin", lambda base_url: "")
    _restore_pin_double(monkeypatch)
    yield tmp_path
    from core.council import api as council_api

    forget = getattr(council_api, "forget_all_runs_for_tests", None)
    if forget is not None:
        forget()
    pin_lock.reset_on_startup()


def _post(path, body, *, host="127.0.0.1"):
    res = dispatch_post(
        path=path, body=body, headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="N"), model_name="vool",
        workspace_root_provider=lambda: "/tmp", client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def _get(path, query=None, *, host="127.0.0.1"):
    res = dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="N"),
                       model_name="vool", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


class Bench:
    """A programmable bench. `script[(round_no, role_id)]` is the text, or an Exception."""

    def __init__(self, script, default=None):
        self.script = script
        self.default = default
        self.dispatched: list[tuple[int, str, str]] = []

    def factory(self, base_url, dispatch_capability=""):
        assert dispatch_capability, "convene built a seat factory with no pin capability"

        def _turn(seat, prompt, round_no, run_id):
            self.dispatched.append((round_no, seat.role_id, seat.model))
            outcome = self.script.get((round_no, seat.role_id), self.default)
            if callable(outcome):
                outcome = outcome(seat, round_no)
            if isinstance(outcome, BaseException):
                raise outcome
            return {"text": outcome, "receipt_count": 1, "session_id": f"s-{seat.seat_id}"}

        return _turn


def _convene(monkeypatch, bench, *, seats=None, max_rounds=5):
    monkeypatch.setattr("core.council.api.live_seat_turn_factory", bench.factory)
    return _post("/api/council/convene", {
        "problem": "a seat dies and the whole bench's work is thrown away",
        "chat_session": CHAT, "seats": seats or BENCH, "max_rounds": max_rounds,
    })


#: States from which nothing further happens without an operator. Reaching one that was
#: not asked for is an immediate failure, not something to wait out: a run that ended
#: `failed` is never going to become `needs_attention`, and a 25s timeout would report
#: that as "slow" instead of "wrong".
_SETTLED = {"converged", "failed", "no_convergence", "stopped", "crashed", "needs_attention"}


def _await_state(run_id, wanted, timeout=20):
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        payload = _get("/api/council/status", {"run": [run_id]})[1]
        seen = str(payload.get("run", {}).get("state") or "")
        if seen in wanted:
            return payload
        if seen in _SETTLED and not payload.get("live"):
            raise AssertionError(
                f"run settled in {seen!r}, which is not one of {sorted(wanted)}; "
                f"outcome={payload.get('run', {}).get('outcome')}"
            )
        time.sleep(0.02)
    raise AssertionError(f"run never reached {sorted(wanted)}; last state {seen!r}")


def _ledger_types(run_id):
    from core.council.run_store import CouncilRunStore

    return [row["type"] for row in CouncilRunStore(run_id).read_events_ordered()]


def _seat_of(payload, role_id):
    return next(s for s in payload["run"]["seats"] if s["role_id"] == role_id)


DIAG = "DIAGNOSIS: the bench's work is thrown away by one dead transport."
AGREE = "VERDICT: AGREE"
DISAGREE = "VERDICT: DISAGREE"


# --------------------------------------------------- 1. the pause, not a false ending


def test_an_exhausted_voting_seat_pauses_the_run_instead_of_ending_it(monkeypatch):
    bench = Bench({(1, "falsifier"): RuntimeError("seat transport died")}, default=DIAG)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    state = _await_state(run_id, {"needs_attention"})

    assert state["run"]["state"] == "needs_attention"
    assert state["live"] is False, "a paused run is not live — nothing is working"
    outcome = state["run"]["outcome"]
    assert outcome["result"] == "needs_attention"
    blocking = outcome["blocking_seats"]
    assert [b["role_id"] for b in blocking] == ["falsifier"]
    assert blocking[0]["model"] == "vendor/beta"
    assert blocking[0]["attempts"] >= 1
    assert blocking[0]["outcome"] in {"FAILED", "EMPTY", "MALFORMED", "TIMED_OUT"}
    assert "seat transport died" in blocking[0]["failure"]
    assert "needs_attention" in _ledger_types(run_id)
    assert "run_failed" not in _ledger_types(run_id), (
        "a run the operator can still rescue must not be recorded as failed"
    )


def test_a_paused_run_hands_the_machine_back_while_it_waits(monkeypatch):
    """A council waiting on a human must not hold every chat on the machine hostage —
    the operator needs the chat to diagnose the seat that just died."""
    from core.council import pin_lock

    bench = Bench({(1, "falsifier"): RuntimeError("dead")}, default=DIAG)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    _await_state(run_id, {"needs_attention"})
    assert pin_lock.snapshot() is None, "a paused council still owns the model pin"
    assert _get("/api/council/lock")[1]["locked"] is False


def test_a_seat_that_dies_in_round_three_keeps_two_rounds_of_evidence(monkeypatch):
    """The case the whole slice exists for: real work behind the failure. Round 1
    investigates, round 2 is inconclusive, round 3 loses a seat."""
    bench = Bench(
        {
            (1, "builder"): DIAG, (1, "falsifier"): "looked around", (1, "reviewer"): "read it",
            (2, "builder"): AGREE, (2, "falsifier"): "no verdict line at all",
            (2, "reviewer"): AGREE,
            (3, "builder"): AGREE, (3, "reviewer"): AGREE,
            (3, "falsifier"): TimeoutError("seat read timed out"),
        }
    )
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    state = _await_state(run_id, {"needs_attention"})

    rounds = state["run"]["rounds"]
    assert len(rounds) == 3, f"three rounds of evidence must survive: {len(rounds)}"
    assert len(rounds[0]["reports"]) == 3 and len(rounds[1]["reports"]) == 3
    assert state["run"]["candidate"], "the candidate from round 1 must still be there"
    blocking = state["run"]["outcome"]["blocking_seats"]
    assert [b["role_id"] for b in blocking] == ["falsifier"]
    assert blocking[0]["outcome"] == "TIMED_OUT"
    assert blocking[0]["round_no"] == 3


# ----------------------------------------------------------- 2. retry, on the same run


def test_retry_redispatches_the_seat_and_never_overwrites_its_dead_attempt(monkeypatch):
    attempts = {"n": 0}

    def _falsifier(seat, round_no):
        attempts["n"] += 1
        return RuntimeError("dead") if attempts["n"] <= 2 else AGREE

    bench = Bench({(1, "falsifier"): _falsifier, (1, "builder"): DIAG,
                   (1, "reviewer"): "read it"},
                  default=AGREE)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    dead = [r for r in before["run"]["rounds"][0]["reports"] if r["status"] == "failed"]
    assert len(dead) == 1
    dead_sha = dead[0]["report_sha256"]

    status, action = _post("/api/council/seat",
                           {"run_id": run_id, "seat_id": dead[0]["seat_id"], "action": "retry"})
    assert status == 200, action
    assert action["ok"] is True and action["action"] == "retry"

    resume_status, resumed = _post("/api/council/resume", {"run_id": run_id})
    assert resume_status == 200, resumed
    after = _await_state(run_id, {"converged"})

    reports = after["run"]["rounds"][0]["reports"]
    still_dead = [r for r in reports if r["status"] == "failed"]
    assert len(still_dead) == 1, "the dead attempt was erased instead of kept"
    assert still_dead[0]["report_sha256"] == dead_sha, "the dead attempt was rewritten"
    assert still_dead[0]["superseded"] is True, "the dead attempt must be marked, not deleted"
    live = [r for r in reports if not r["superseded"] and r["seat_id"] == dead[0]["seat_id"]]
    assert len(live) == 1 and live[0]["status"] == "landed"
    types = _ledger_types(run_id)
    assert "seat_retry_requested" in types and "run_resumed" in types


def test_resume_continues_the_same_run_and_writes_no_second_chat_message(monkeypatch):
    from core.persistent_memory import recent_conversation_events

    attempts = {"n": 0}

    def _falsifier(seat, round_no):
        attempts["n"] += 1
        return RuntimeError("dead") if attempts["n"] <= 2 else AGREE

    bench = Bench({(1, "falsifier"): _falsifier, (1, "builder"): DIAG,
                   (1, "reviewer"): "read it"}, default=AGREE)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    _await_state(run_id, {"needs_attention"})
    assert recent_conversation_events(CHAT, limit=10, include_artifacts=True) == [], (
        "a paused run is not finished and must not write its verdict yet"
    )
    seat_id = next(r["seat_id"] for r in
                   _get("/api/council/status", {"run": [run_id]})[1]["run"]["rounds"][0]["reports"]
                   if r["status"] == "failed")
    _post("/api/council/seat", {"run_id": run_id, "seat_id": seat_id, "action": "retry"})
    status, resumed = _post("/api/council/resume", {"run_id": run_id})
    assert status == 200 and resumed["run_id"] == run_id, resumed
    _await_state(run_id, {"converged"})

    rows = recent_conversation_events(CHAT, limit=10, include_artifacts=True)
    assert len(rows) == 1, "a resumed run wrote more than one council message"
    assert f"council | run={run_id}" in rows[0]["assistant"], (
        "the resumed run wrote its verdict under a different run id"
    )
    listed = _get("/api/council/runs")[1]["runs"]
    assert [r["run_id"] for r in listed].count(run_id) == 1
    assert len(listed) == 1, "resume created a second hidden run"


# --------------------------------------------------------------- 3. replace the model


def test_replacing_a_model_changes_the_model_and_nothing_else(monkeypatch):
    attempts = {"n": 0}

    def _falsifier(seat, round_no):
        attempts["n"] += 1
        if seat.model == "vendor/beta":
            return RuntimeError("this provider is down")
        return AGREE

    bench = Bench({(1, "falsifier"): _falsifier, (1, "builder"): DIAG,
                   (1, "reviewer"): "read it"}, default=AGREE)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")

    status, action = _post("/api/council/seat", {
        "run_id": run_id, "seat_id": seat["seat_id"], "action": "replace",
        "model": "vendor/delta",
    })
    assert status == 200, action
    _post("/api/council/resume", {"run_id": run_id})
    after = _await_state(run_id, {"converged"})

    seat_after = _seat_of(after, "falsifier")
    assert seat_after["model"] == "vendor/delta"
    assert seat_after["role_id"] == "falsifier", "replacement changed the seat's ROLE"
    assert seat_after["votes"] is True, "replacement changed the seat's vote"
    assert seat_after["seat_id"] == seat["seat_id"]
    dead = [r for r in after["run"]["rounds"][0]["reports"]
            if r["seat_id"] == seat["seat_id"] and r["status"] == "failed"]
    assert dead and dead[0]["model_requested"] == "vendor/beta", (
        "the dead attempt's model was rewritten to the replacement"
    )
    assert (3, "falsifier", "vendor/delta") in [
        (r, role, m) for (r, role, m) in bench.dispatched
    ] or any(m == "vendor/delta" for (_, role, m) in bench.dispatched if role == "falsifier")
    types = _ledger_types(run_id)
    assert "seat_model_replaced" in types


def test_a_replacement_model_is_refused_when_it_is_not_a_cloud_seat(monkeypatch):
    """The cloud-only seat wall is server-side and applies to a replacement exactly as it
    applies at convene — the operator cannot smuggle a local model in through this door."""
    bench = Bench({(1, "falsifier"): RuntimeError("dead")}, default=DIAG)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")
    for bad in ("auto", "vool", "llama3", ""):
        status, action = _post("/api/council/seat", {
            "run_id": run_id, "seat_id": seat["seat_id"], "action": "replace", "model": bad})
        assert status == 400, (bad, action)
        assert "local_seat_refused" in action["error"] or "model" in action["error"], action
    assert _seat_of(_get("/api/council/status", {"run": [run_id]})[1], "falsifier")["model"] == (
        "vendor/beta"
    ), "a refused replacement changed the seat anyway"


# ----------------------------------------------------- 4. disable, and the quorum law


def test_disabling_a_seat_recomputes_quorum_from_the_seats_that_remain(monkeypatch):
    bench = Bench({(1, "falsifier"): RuntimeError("dead"), (1, "builder"): DIAG,
                   (1, "reviewer"): "read it"}, default=AGREE)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")

    status, action = _post("/api/council/seat",
                           {"run_id": run_id, "seat_id": seat["seat_id"], "action": "disable"})
    assert status == 200, action
    _post("/api/council/resume", {"run_id": run_id})
    after = _await_state(run_id, {"converged"})

    assert after["run"]["state"] == "converged", after["run"]["outcome"]
    assert after["run"]["outcome"]["agree"] == 2, (
        "the tally must be recomputed over the ACTIVE voting seats: "
        + str(after["run"]["outcome"])
    )
    disabled = _seat_of(after, "falsifier")
    assert disabled["active"] is False
    assert disabled["votes"] is True, (
        "disable must not rewrite a seat's vote — a disabled judge is not an advisor"
    )
    for role in ("builder", "reviewer"):
        assert _seat_of(after, role)["votes"] is True, (
            "disabling one seat silently changed another seat's vote"
        )
    assert "seat_disabled" in _ledger_types(run_id)
    assert [r["seat_id"] for r in after["run"]["rounds"][0]["reports"]].count(
        seat["seat_id"]) == 1, "the disabled seat's history was deleted"


def test_a_disable_that_would_leave_no_quorum_is_refused(monkeypatch):
    bench = Bench({(1, "builder"): RuntimeError("dead")},
                  default=RuntimeError("dead"))
    status, payload = _convene(monkeypatch, bench, seats=[
        {"role_id": "builder", "model": "vendor/alpha", "votes": True},
        {"role_id": "verifier", "model": "vendor/gamma", "votes": False},
    ])
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "builder")

    status, action = _post("/api/council/seat",
                           {"run_id": run_id, "seat_id": seat["seat_id"], "action": "disable"})
    assert status == 409, action
    assert action["error"] == "quorum_impossible"
    assert "advisor" in action["detail"].lower() or "voting" in action["detail"].lower()
    still = _seat_of(_get("/api/council/status", {"run": [run_id]})[1], "builder")
    assert still["active"] is True, "a refused disable took effect anyway"


# ------------------------------------------------- 5. typed, idempotent, owner-only


def test_the_same_action_twice_is_idempotent_and_never_double_dispatches(monkeypatch):
    bench = Bench({(1, "falsifier"): RuntimeError("dead"), (1, "builder"): DIAG,
                   (1, "reviewer"): "read it"}, default=AGREE)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")

    first = _post("/api/council/seat",
                  {"run_id": run_id, "seat_id": seat["seat_id"], "action": "retry"})
    second = _post("/api/council/seat",
                   {"run_id": run_id, "seat_id": seat["seat_id"], "action": "retry"})
    assert first[0] == 200 and second[0] == 200, (first, second)
    assert first[1]["changed"] is True and second[1]["changed"] is False, (first, second)
    assert _ledger_types(run_id).count("seat_retry_requested") == 1, (
        "a repeated action wrote a second ledger row for work that happened once"
    )


def test_every_operator_action_is_typed_and_owner_local(monkeypatch):
    bench = Bench({(1, "falsifier"): RuntimeError("dead")}, default=DIAG)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")

    for path, body in (
        ("/api/council/seat", {"run_id": run_id, "seat_id": seat["seat_id"], "action": "retry"}),
        ("/api/council/resume", {"run_id": run_id}),
    ):
        status, action = _post(path, body, host="203.0.113.9")
        assert status == 403 and action["error"] == "owner_local_required", (path, action)

    bad = [
        ({"run_id": run_id, "seat_id": seat["seat_id"], "action": "explode"}, "unknown action"),
        ({"run_id": run_id, "seat_id": "nope", "action": "retry"}, "seat"),
        ({"run_id": "council-nosuchrun", "seat_id": "s1", "action": "retry"}, "run"),
        ({"run_id": run_id, "seat_id": seat["seat_id"], "action": "retry", "surprise": 1},
         "unknown fields"),
    ]
    for body, expected in bad:
        status, action = _post("/api/council/seat", body)
        assert status in (400, 404, 409), (body, action)
        assert expected.split()[0] in json.dumps(action).lower(), (body, action)


def test_a_stop_beats_a_resume_that_is_racing_it(monkeypatch):
    """Cancellation wins. An operator who stopped the run must not have it restarted by a
    resume that was already in flight."""
    bench = Bench({(1, "falsifier"): RuntimeError("dead"), (1, "builder"): DIAG,
                   (1, "reviewer"): "read it"}, default=AGREE)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")
    _post("/api/council/seat", {"run_id": run_id, "seat_id": seat["seat_id"], "action": "retry"})
    # Two dispatches already happened: the bounded automatic retry, before the pause. What
    # must not happen is a THIRD, caused by a resume that lost the race with a stop.
    dispatched_at_pause = len(bench.dispatched)

    stop_status, stop_payload = _post("/api/council/stop", {"run_id": run_id})
    assert stop_status == 200, stop_payload
    resume_status, resume_payload = _post("/api/council/resume", {"run_id": run_id})
    assert resume_status == 409, resume_payload
    assert resume_payload["error"] == "run_stopped", resume_payload
    final = _await_state(run_id, {"stopped"})
    assert final["run"]["state"] == "stopped"
    assert len(bench.dispatched) == dispatched_at_pause, (
        "the stopped run dispatched a seat again anyway: "
        + repr(bench.dispatched[dispatched_at_pause:])
    )


def test_a_second_council_cannot_convene_while_one_is_paused(monkeypatch):
    """A paused run still owns the bench, the chat card and the right to resume. A second
    convene would strand it with no way back."""
    bench = Bench({(1, "falsifier"): RuntimeError("dead")}, default=DIAG)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    _await_state(run_id, {"needs_attention"})
    status, second = _convene(monkeypatch, Bench({}, default=DIAG))
    assert status == 409, second
    assert second["error"] == "council_needs_attention"
    assert second["run_id"] == run_id


def test_an_action_on_a_run_that_did_not_survive_a_restart_is_typed(monkeypatch):
    """Pause state lives in this process. A state file that says needs_attention after a
    restart describes a run whose thread is gone — the controls must say so, not hang."""
    from core.council import api as council_api

    bench = Bench({(1, "falsifier"): RuntimeError("dead")}, default=DIAG)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")
    council_api.forget_all_runs_for_tests()   # what a daemon restart leaves behind

    status, action = _post("/api/council/seat",
                           {"run_id": run_id, "seat_id": seat["seat_id"], "action": "retry"})
    assert status == 409, action
    assert action["error"] == "council_run_not_resumable"
    status, resumed = _post("/api/council/resume", {"run_id": run_id})
    assert status == 409 and resumed["error"] == "council_run_not_resumable"


def test_a_resume_takes_the_pin_back_through_the_existing_fence(monkeypatch):
    """C3c stays authoritative: a resumed run is a pin owner like any other, and an
    ordinary turn during it is refused by the same typed 409."""
    from core.council import pin_lock

    gate = threading.Event()
    bench = Bench(
        {(1, "falsifier"): RuntimeError("dead"), (1, "builder"): DIAG, (1, "reviewer"): "read it",
         (2, "builder"): lambda seat, r: (gate.wait(20), AGREE)[1]},
        default=AGREE,
    )
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    before = _await_state(run_id, {"needs_attention"})
    seat = _seat_of(before, "falsifier")
    assert pin_lock.snapshot() is None
    _post("/api/council/seat", {"run_id": run_id, "seat_id": seat["seat_id"], "action": "disable"})
    resume_status, resumed = _post("/api/council/resume", {"run_id": run_id})
    assert resume_status == 200, resumed

    deadline = time.time() + 10
    while time.time() < deadline and pin_lock.snapshot() is None:
        time.sleep(0.02)
    owned = pin_lock.snapshot()
    assert owned is not None and owned["run_id"] == run_id, (
        "a resumed run did not take the model pin back"
    )
    status, refused = _post("/api/chat", {
        "messages": [{"role": "user", "content": "hi"}], "session_id": CHAT, "turn_id": "t1"})
    assert status == 409 and refused["error"] == "council_model_pin_active"
    gate.set()
    _await_state(run_id, {"converged", "no_convergence", "needs_attention", "failed"})


def test_polling_a_paused_run_never_invents_output_for_the_dead_seat(monkeypatch):
    bench = Bench({(1, "falsifier"): RuntimeError("dead"), (1, "builder"): DIAG,
                   (1, "reviewer"): "read it"}, default=AGREE)
    status, payload = _convene(monkeypatch, bench)
    assert status == 200, payload
    run_id = payload["run_id"]
    first = _await_state(run_id, {"needs_attention"})
    time.sleep(0.15)
    second = _get("/api/council/status", {"run": [run_id]})[1]
    dead_first = [r for r in first["run"]["rounds"][0]["reports"] if r["status"] == "failed"]
    dead_second = [r for r in second["run"]["rounds"][0]["reports"] if r["status"] == "failed"]
    assert dead_first == dead_second, "a repeated poll changed a dead seat's record"
    assert dead_second[0]["text_ref"] is None or dead_second[0].get("report_sha256") is None, (
        "a failed seat was given report bytes it never produced"
    )
    assert second["live"] is False


def test_the_instant_a_run_reports_converged_its_chat_summary_already_exists(monkeypatch, tmp_path, isolated):
    """The finalization contract (Goal 2, 2026-09-18), pinning the documented finalize-ordering
    defect: ``_finish`` used to persist ``converged`` before the thread's ``finally`` wrote the
    chat summary, so a run could read finished with no final artifact (this suite's own resume
    test observed exactly that once). The repair publishes the terminal summary BEFORE the
    terminal state becomes visible; the thread's idempotent re-run must not double it."""
    from core.council.run_store import CouncilRunStore

    bench = Bench(script={}, default="VERDICT: AGREE")
    monkeypatch.setattr("core.council.api.live_seat_turn_factory", bench.factory)
    status, body = _post("/api/council/convene", {
        "problem": "finalize-ordering pin",
        "chat_session": CHAT,
        "seats": BENCH,
        "max_rounds": 3,
    })
    assert status == 200, body
    run_id = body["run_id"]
    _await_state(run_id, {"converged"})
    events = [row["type"] for row in CouncilRunStore(run_id).read_events_ordered()]
    assert "chat_summary_persisted" in events, (
        f"run {run_id} reported converged with no chat summary in its ledger ({events}); "
        "completion was published before its artifact -- the finalize-ordering defect"
    )
    import time as _time
    _deadline = _time.time() + 10
    while _time.time() < _deadline:
        _payload = _get("/api/council/status", {"run": [run_id]})[1]
        if not _payload.get("live"):
            break
        _time.sleep(0.05)
    kinds = [row["type"] for row in CouncilRunStore(run_id).read_events_ordered()]
    assert kinds.count("chat_summary_persisted") == 1, kinds
