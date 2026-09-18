"""C3b — the council's model pin is fenced by the SERVER, not by a disabled textarea.

A council swaps ONE server-global composer pin per cloud seat. C3 closed the originating
chat's composer, which is a promise the browser makes on the server's behalf and cannot
keep: a second tab, a different chat, curl, or any client that never loaded the page could
POST /api/chat mid-run and be answered by whichever seat model happened to be pinned at
that instant — silently, with the right session id and a normal 200.

This suite drives the boundary itself. Worst cases, not greetings: a caller who knows the
run id, a caller who mints a canonical seat session id by hand, a crashed run, a restart
over a persisted "running" state, and ordinary turns racing convene for the pin.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

CHAT = "openclaw:aaaaaaaaaaaaaaaaaaaa"
OTHER_CHAT = "openclaw:bbbbbbbbbbbbbbbbbbbb"

CLOUD_BENCH = [
    {"role_id": "builder", "model": "vendor/model-a", "votes": True},
    {"role_id": "falsifier", "model": "vendor/model-b", "votes": True},
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    tmpdir_holder["path"] = str(tmp_path)

    def _patched(*parts):
        base = tmp_path / "council-data"
        out = base
        for part in parts:
            out = out / part
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    monkeypatch.setattr("core.council.api.read_current_pin", lambda base_url: "")
    _restore_pin_double(monkeypatch)
    from core.council import api as council_api
    from core.council import pin_lock

    # A run that pauses at NEEDS_ATTENTION stays in the paused registry, and a paused run
    # refuses the next convene. Clearing both registries is what keeps one test's rescue
    # from reading as the next test's typed refusal.
    council_api.forget_all_runs_for_tests()
    pin_lock.reset_on_startup()
    yield tmp_path
    council_api.forget_all_runs_for_tests()
    pin_lock.reset_on_startup()


def _restore_pin_double(monkeypatch):
    """A restore_pin stand-in that CHECKS rather than absorbs: the operator's pin is handed
    back while the run still owns the fence, so the call must carry the run's capability or
    the council's own restoration would be refused by its own guard."""
    from core.council import pin_lock

    def _restore(base_url, model, capability=""):
        assert capability, "restore_pin was called without the run's pin capability"
        assert pin_lock.is_dispatch_capability(capability, owner_local=True), (
            "restore_pin carried something that is not the live run's capability"
        )

    monkeypatch.setattr("core.council.api.restore_pin", _restore)


def _runtime():
    return RuntimeServices(display_name="N")


def _post(path, body, *, host="127.0.0.1", headers=None, run_agent_provider=None):
    kwargs = {}
    if run_agent_provider is not None:
        kwargs["run_agent_provider"] = run_agent_provider
    res = dispatch_post(
        path=path, body=body,
        headers={"content-type": "application/json", **(headers or {})},
        runtime=_runtime(), model_name="vool",
        workspace_root_provider=lambda: str(tmpdir_holder["path"]),
        client_host=host, **kwargs,
    )
    payload = json.loads(res.body.decode("utf-8")) if res.body else {}
    return res.status, payload


def _get(path, query=None, *, host="127.0.0.1"):
    res = dispatch_get(path=path, query=query or {}, runtime=_runtime(),
                       model_name="vool", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


tmpdir_holder: dict = {"path": "/tmp"}


def _chat_body(session_id, text="what model am I talking to?"):
    return {
        "messages": [{"role": "user", "content": text}],
        "session_id": session_id,
        "turn_id": "t-" + session_id[-6:],
    }


def _recording_agent():
    """A run_agent stand-in that records the pin state AT THE MOMENT it is called.

    This is the whole measurement: "was a model selected while a council owned the pin"
    is answered by what the runtime saw when it ran, not by a status code afterwards.
    """
    calls: list[dict] = []

    def _agent(runtime, user_text, *, session_id, source_context, workspace_root_provider, **_):
        from core.council import pin_lock

        calls.append({"session_id": session_id, "pin": pin_lock.snapshot()})
        return {"response": "ok", "confidence": 1.0}

    return _agent, calls


def _convene(chat_session=CHAT, seats=None):
    status, payload = _post(
        "/api/council/convene",
        {"problem": "the pin fence is a promise the browser cannot keep",
         "chat_session": chat_session, "seats": seats or CLOUD_BENCH},
    )
    return status, payload


@pytest.fixture()
def parked_council(monkeypatch):
    """A council that has taken the pin and stays there until the test releases it.

    The seat turn blocks on an Event, so the run is genuinely mid-flight — not a run
    state file that merely says so.
    """
    release = threading.Event()
    dispatched: list[str] = []

    def _factory(base_url, *args, **kwargs):
        def _turn(seat, prompt, round_no, run_id):
            dispatched.append(seat.seat_id)
            release.wait(20)
            return {"text": "DIAGNOSIS: parked.", "receipt_count": 0, "session_id": "s"}

        return _turn

    monkeypatch.setattr("core.council.api.live_seat_turn_factory", _factory)
    status, payload = _convene()
    assert status == 200 and payload.get("ok"), payload
    deadline = time.time() + 10
    while not dispatched and time.time() < deadline:
        time.sleep(0.02)
    assert dispatched, "the council never dispatched a seat — it never took the pin"
    yield payload["run_id"], release
    release.set()
    deadline = time.time() + 20
    while time.time() < deadline:
        from core.council import pin_lock

        if pin_lock.snapshot() is None:
            break
        time.sleep(0.02)


# ------------------------------------------------- 1. no ordinary turn gets through


def test_the_originating_chat_cannot_send_an_ordinary_turn_during_a_council(parked_council):
    run_id, _ = parked_council
    agent, calls = _recording_agent()
    status, payload = _post("/api/chat", _chat_body(CHAT), run_agent_provider=agent)
    assert status == 409, payload
    assert payload["error"] == "council_model_pin_active"
    assert payload["run_id"] == run_id
    assert payload.get("state")
    assert payload.get("retry_after_state"), "the caller must be told what to wait for"
    assert calls == [], "a model was selected for a refused turn"


def test_a_different_chat_and_a_second_tab_are_fenced_too(parked_council):
    """The pin is global. The C3 composer lock was per chat, so this is exactly the
    hole it left: another chat, another tab, or a client that never loaded the page."""
    agent, calls = _recording_agent()
    for session in (OTHER_CHAT, "openclaw:cccccccccccccccccccc", "some-external-caller"):
        status, payload = _post("/api/chat", _chat_body(session), run_agent_provider=agent)
        assert status == 409, (session, payload)
        assert payload["error"] == "council_model_pin_active"
    assert calls == [], "a model was selected for a chat the browser never fenced"


def test_the_openai_compatible_lane_is_fenced_on_the_same_terms(parked_council):
    agent, calls = _recording_agent()
    status, payload = _post("/v1/chat/completions", _chat_body(OTHER_CHAT), run_agent_provider=agent)
    assert status == 409 and payload["error"] == "council_model_pin_active"
    assert calls == []


# ---------------------------------------- 2. only the council's own dispatch passes


def test_a_valid_seat_dispatch_capability_passes_the_fence(parked_council):
    from core.council import pin_lock

    capability = pin_lock.dispatch_capability_for_tests()
    agent, calls = _recording_agent()
    status, payload = _post(
        "/api/chat", _chat_body("openclaw:dddddddddddddddddddd"),
        headers={"X-Vool-Council-Dispatch": capability},
        run_agent_provider=agent,
    )
    assert status == 200, payload
    assert len(calls) == 1, "the council's own seat turn must still run"


def test_knowing_the_run_id_or_forging_a_seat_session_id_buys_nothing(parked_council):
    """The two things a caller CAN learn: the run id (it is in /api/council/status) and
    the seat session id (it is derived from public material). Neither is a capability."""
    import hashlib

    run_id, _ = parked_council
    forged_seat_session = (
        "openclaw:" + hashlib.sha256(f"council-seat:{run_id}:s1".encode()).hexdigest()[:20]
    )
    agent, calls = _recording_agent()
    attempts = [
        ({}, forged_seat_session),
        ({"X-Vool-Council-Dispatch": run_id}, CHAT),
        ({"X-Vool-Council-Dispatch": ""}, CHAT),
        ({"X-Vool-Council-Dispatch": "council-dispatch"}, forged_seat_session),
    ]
    for headers, session in attempts:
        status, payload = _post("/api/chat", _chat_body(session), headers=headers,
                                run_agent_provider=agent)
        assert status == 409, (headers, payload)
        assert payload["error"] == "council_model_pin_active"
    assert calls == [], "a guessed identity selected a model under the seat pin"


def test_a_remote_caller_holding_the_capability_is_still_refused(parked_council):
    """A capability that leaked off the machine is not a key to the front door: the
    bypass is loopback-only, exactly like every other council surface."""
    from core.council import pin_lock

    agent, calls = _recording_agent()
    status, payload = _post(
        "/api/chat", _chat_body(OTHER_CHAT), host="203.0.113.9",
        headers={"X-Vool-Council-Dispatch": pin_lock.dispatch_capability_for_tests()},
        run_agent_provider=agent,
    )
    assert status == 409 and payload["error"] == "council_model_pin_active"
    assert calls == []


# ---------------------------------------------------- 3. the capability never leaks


def test_no_council_read_surface_ever_carries_the_capability(parked_council):
    from core.council import pin_lock
    from core.council.run_store import CouncilRunStore

    run_id, _ = parked_council
    capability = pin_lock.dispatch_capability_for_tests()
    assert capability, "there is no capability to check for"

    seen = []
    for path, query in (
        ("/api/council/status", {"run": [run_id]}),
        ("/api/council/events", {"run": [run_id]}),
        ("/api/council/runs", {}),
        ("/api/council/lock", {}),
        ("/api/chat/history", {"session": [CHAT]}),
    ):
        status, payload = _get(path, query)
        assert status == 200, (path, payload)
        seen.append(json.dumps(payload))
    for blob in seen:
        assert capability not in blob, "a read surface serves the dispatch capability"

    store = CouncilRunStore(run_id)
    assert capability not in json.dumps(store.read_state() or {})
    assert capability not in json.dumps(store.read_events())


def test_the_lock_surface_states_the_run_without_handing_out_the_key(parked_council):
    run_id, _ = parked_council
    status, payload = _get("/api/council/lock")
    assert status == 200 and payload["ok"] is True
    assert payload["locked"] is True
    assert payload["run_id"] == run_id
    assert payload.get("reason"), "a locked composer must carry the reason to show"
    assert "capability" not in json.dumps(payload)
    assert _get("/api/council/lock", host="203.0.113.9")[0] == 403, "owner-local like the rest"


# --------------------------------------------------- 4. the lock ends when the run does


@pytest.mark.parametrize(
    "terminal", ["converged", "needs_attention", "no_convergence", "stopped"])
def test_every_run_ending_hands_the_pin_back(monkeypatch, terminal):
    """Driven through the real council thread, not by calling release() by hand.

    `failed` was one of these until C4, when a voting seat exhausting its attempts stopped
    being a dead end and became NEEDS_ATTENTION — a pause the operator owns. A pause hands
    the machine back exactly as a terminal ending does, and for a sharper reason: the
    operator needs the chat to diagnose the seat that just died."""
    from core.council import pin_lock

    def _factory(base_url, *args, **kwargs):
        def _turn(seat, prompt, round_no, run_id):
            if terminal == "needs_attention":
                raise RuntimeError("seat transport died")
            if terminal == "no_convergence":
                return {"text": "DIAGNOSIS: x" if round_no == 1 else "VERDICT: DISAGREE"}
            return {"text": "DIAGNOSIS: x" if round_no == 1 else "VERDICT: AGREE"}

        return _turn

    monkeypatch.setattr("core.council.api.live_seat_turn_factory", _factory)
    if terminal == "stopped":
        monkeypatch.setattr("core.council.api.live_seat_turn_factory",
                            lambda base_url, *a, **k: (lambda *args: {"text": "DIAGNOSIS: x"}))
    status, payload = _convene(seats=[{"role_id": "builder", "model": "vendor/a", "votes": True}]
                               if terminal != "no_convergence" else CLOUD_BENCH)
    assert status == 200, payload
    run_id = payload["run_id"]
    if terminal == "stopped":
        _post("/api/council/stop", {"run_id": run_id})
    deadline = time.time() + 25
    while time.time() < deadline and pin_lock.snapshot() is not None:
        time.sleep(0.02)
    assert pin_lock.snapshot() is None, f"{terminal} left the pin locked forever"
    agent, calls = _recording_agent()
    assert _post("/api/chat", _chat_body(CHAT), run_agent_provider=agent)[0] == 200
    assert len(calls) == 1


def test_a_crashed_council_thread_still_hands_the_pin_back(monkeypatch):
    """The orchestrator raising is the path where a `finally` is the only thing between
    the operator and a chat that is dead until the daemon restarts."""
    from core.council import pin_lock

    def _factory(base_url, *args, **kwargs):
        def _turn(seat, prompt, round_no, run_id):
            return {"text": "DIAGNOSIS: x"}

        return _turn

    monkeypatch.setattr("core.council.api.live_seat_turn_factory", _factory)
    monkeypatch.setattr(
        "core.council.orchestrator.CouncilOrchestrator.run",
        lambda self: (_ for _ in ()).throw(RuntimeError("orchestrator exploded")),
    )
    status, payload = _convene()
    assert status == 200, payload
    deadline = time.time() + 20
    while time.time() < deadline and pin_lock.snapshot() is not None:
        time.sleep(0.02)
    assert pin_lock.snapshot() is None, "a crashed council owns the pin forever"
    assert _get("/api/council/lock")[1]["locked"] is False


def test_a_persisted_running_run_with_no_live_owner_never_locks_chat():
    """Restart recovery. The pin is a fact about THIS process; a state file that says
    round_open describes a run whose thread died with the last daemon."""
    from core.council import pin_lock
    from core.council.run_store import CouncilRunStore

    store = CouncilRunStore("council-ghost0001")
    store.write_state({
        "state": "round_open", "problem": "p", "chat_session": CHAT, "max_rounds": 5,
        "started_at": 1000.0, "round_no": 2, "seats": [], "rounds": [], "outcome": {},
    })
    pin_lock.reset_on_startup()
    assert pin_lock.snapshot() is None
    assert _get("/api/council/lock")[1]["locked"] is False
    # And the runs listing, which DOES read that file, must not resurrect a lock from it.
    assert _get("/api/council/runs")[1]["ok"] is True
    assert pin_lock.snapshot() is None
    agent, calls = _recording_agent()
    assert _post("/api/chat", _chat_body(CHAT), run_agent_provider=agent)[0] == 200
    assert len(calls) == 1


# ------------------------------------------------------------------- 5. the race


def test_ordinary_turns_racing_convene_are_never_answered_under_a_seat_pin(monkeypatch):
    """The check-then-act window: a turn passes the fence, the council takes the pin,
    and the turn then selects its model — under a seat's pin. Both outcomes are legal
    (admitted before ownership, or refused before selection); being selected while a
    council owns the pin is not, and the recording agent reports which happened."""
    from core.council import pin_lock

    started = threading.Event()
    calls: list[dict] = []
    calls_lock = threading.Lock()

    def _agent(runtime, user_text, *, session_id, source_context, workspace_root_provider, **_):
        started.set()
        with calls_lock:
            calls.append({"pin": pin_lock.snapshot()})
        time.sleep(0.12)   # widen the window the council would otherwise pin inside
        return {"response": "ok", "confidence": 1.0}

    def _factory(base_url, *args, **kwargs):
        return lambda seat, prompt, round_no, run_id: {"text": "DIAGNOSIS: x"}

    monkeypatch.setattr("core.council.api.live_seat_turn_factory", _factory)

    results: list[int] = []
    results_lock = threading.Lock()

    def _ordinary(index):
        status, _ = _post("/api/chat", _chat_body(f"openclaw:{index:020d}"),
                          run_agent_provider=_agent)
        with results_lock:
            results.append(status)

    threads = [threading.Thread(target=_ordinary, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    started.wait(5)
    convene_status, convene_payload = _convene(
        seats=[{"role_id": "builder", "model": "vendor/a", "votes": True}])
    for thread in threads:
        thread.join(30)

    assert convene_status in (200, 409), convene_payload
    assert results and len(results) == 6
    assert set(results) <= {200, 409}, results
    # Not vacuous: every admitted turn reached the agent, and at least one did — so the
    # pin assertion below is a measurement, not an empty loop over a refused batch.
    assert len(calls) == results.count(200) == sum(1 for r in results if r == 200), (results, len(calls))
    assert len(calls) >= 1, (
        "every request was refused, so nothing measured whether a model can be selected "
        "under a seat pin — widen the race window"
    )
    for call in calls:
        assert call["pin"] is None, (
            "an ordinary turn selected its model while a council owned the pin"
        )
    if convene_status == 200:
        deadline = time.time() + 25
        while time.time() < deadline and pin_lock.snapshot() is not None:
            time.sleep(0.02)
        assert pin_lock.snapshot() is None


def test_convene_refuses_rather_than_pinning_over_a_turn_that_will_not_drain(monkeypatch):
    """The other half of the race law: if an ordinary turn cannot be drained inside the
    bound, the council does NOT take the pin anyway. It says so and stays out."""
    from core.council import pin_lock

    monkeypatch.setattr(pin_lock, "_DRAIN_TIMEOUT_SECONDS", 0.2)
    admission = pin_lock.admit_chat_turn(capability="", owner_local=True)
    assert admission.refusal is None, "nothing owns the pin yet"
    try:
        monkeypatch.setattr("core.council.api.live_seat_turn_factory",
                            lambda base_url, *a, **k: (lambda *args: {"text": "DIAGNOSIS: x"}))
        status, payload = _convene()
        assert status == 409, payload
        assert payload["error"] == "chat_turn_in_flight"
        assert pin_lock.snapshot() is None, "convene pinned anyway after refusing"
    finally:
        admission.release()


# =====================================================================================
# C3c — the two races C3b left open: a live turn evicted by the clock, and a second
# writer to the same global pin that never touched the fence at all.
# =====================================================================================


def _set_cloud_model_spy(monkeypatch):
    """Watch the ONE mutation authority. 'Was the pin written' is answered by whether
    this ran, not by a status code that could be right for the wrong reason."""
    calls: list[dict] = []

    def _fake(model_or_keyword, *, provider=None, owner_local=False, confirm_paid=False, **kw):
        from core.council import pin_lock

        calls.append({"model": model_or_keyword, "pin": pin_lock.snapshot()})
        return True, "switched", str(model_or_keyword)

    monkeypatch.setattr("core.cloud_model_control.set_cloud_model", _fake)
    return calls


# ------------------------------------------------- A. a live turn is never evicted


def test_a_long_running_turn_is_never_evicted_by_the_clock(monkeypatch):
    """VOOL turns run for hours. C3b forgave any admission older than 45 minutes, so a
    turn still streaming was dropped from the count and a council pinned straight over
    it — the exact defect the fence exists to prevent, on a timer."""
    import time as _time

    from core.council import pin_lock

    admission = pin_lock.admit_chat_turn(capability="", owner_local=True)
    assert admission.refusal is None
    try:
        # Twelve hours in, still streaming, never released.
        with pin_lock._CONDITION:
            pin_lock._admissions[admission.token] = _time.monotonic() - 12 * 3600
        assert pin_lock.inflight_admissions() == 1, (
            "a turn that has not released is still running, whatever the clock says"
        )
        monkeypatch.setattr(pin_lock, "_DRAIN_TIMEOUT_SECONDS", 0.2)
        with pytest.raises(pin_lock.CouncilPinBusyError) as caught:
            pin_lock.acquire("council-agetest0001")
        assert caught.value.code == "chat_turn_in_flight"
        assert pin_lock.snapshot() is None, "the council pinned through a live turn"
    finally:
        admission.release()
    # Released, the same acquire succeeds: it was the LIVE turn holding it off, not a bug.
    capability = pin_lock.acquire("council-agetest0001")
    assert capability and pin_lock.snapshot()["run_id"] == "council-agetest0001"
    pin_lock.release("council-agetest0001")


def test_release_is_what_frees_an_admission_and_it_is_idempotent():
    from core.council import pin_lock

    admission = pin_lock.admit_chat_turn(capability="", owner_local=True)
    assert pin_lock.inflight_admissions() == 1
    admission.release()
    admission.release()
    admission.release()
    assert pin_lock.inflight_admissions() == 0, "a double release must not go negative"


def test_a_convene_refusal_says_how_many_turns_are_holding_it_off(monkeypatch):
    from core.council import pin_lock

    held = [pin_lock.admit_chat_turn(capability="", owner_local=True) for _ in range(3)]
    try:
        monkeypatch.setattr(pin_lock, "_DRAIN_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr("core.council.api.live_seat_turn_factory",
                            lambda base_url, *a, **k: (lambda *args: {"text": "DIAGNOSIS: x"}))
        status, payload = _convene()
        assert status == 409 and payload["error"] == "chat_turn_in_flight"
        assert "3" in str(payload.get("detail") or ""), (
            "the operator is told nothing about what is holding the council off: "
            + str(payload.get("detail"))
        )
    finally:
        for admission in held:
            admission.release()


# ------------------------------------- B. the second writer to the same global pin


def test_an_ordinary_model_write_is_refused_while_a_council_owns_the_pin(parked_council, monkeypatch):
    """POST /api/cloud/model is the SAME global state the council is holding. C3b fenced
    turn execution and left the pin's own setter wide open."""
    run_id, _ = parked_council
    calls = _set_cloud_model_spy(monkeypatch)
    status, payload = _post("/api/cloud/model", {"model": "vendor/other-model"})
    assert status == 409, payload
    assert payload["error"] == "council_model_pin_active"
    assert payload["run_id"] == run_id
    assert payload.get("retry_after_state")
    assert calls == [], "the pin was rewritten under the council"


def test_the_refusal_lands_before_any_classification_or_pricing_work(parked_council, monkeypatch):
    """A refusal that first classifies the model has already done the work it refused,
    and leaks pricing state for an id the caller may not pin."""
    classified: list[str] = []
    monkeypatch.setattr(
        "core.cloud_model_control.classify_cloud_model_cost",
        lambda *a, **kw: classified.append(a[0] if a else "") or {
            "cost_state": "free", "reason_code": "", "model": "x"},
    )
    calls = _set_cloud_model_spy(monkeypatch)
    status, payload = _post("/api/cloud/model", {"model": "vendor/paid-model"})
    assert status == 409 and payload["error"] == "council_model_pin_active"
    assert classified == [], "the endpoint classified a model it was about to refuse"
    assert calls == []


def test_the_council_capability_still_pins_a_seat_model_and_restores_the_operator_pin(
    parked_council, monkeypatch
):
    """The council's own two writes — the per-seat pin and the final restoration — must
    keep working, through the SAME capability, never a second secret."""
    from core.council import dispatch, pin_lock

    capability = pin_lock.dispatch_capability_for_tests()
    calls = _set_cloud_model_spy(monkeypatch)
    status, payload = _post(
        "/api/cloud/model", {"model": "default"},
        headers={"X-Vool-Council-Dispatch": capability},
    )
    assert status == 200, payload
    assert [c["model"] for c in calls] == ["default"], (
        "the council's own write did not reach the mutation authority"
    )
    assert calls[0]["pin"] is not None, "the council should still own the pin for its own write"

    # And through the dispatcher's own two helpers, which is how the run really writes.
    posted: list[dict] = []

    def _fake_post(base_url, path, body, timeout=30.0, capability=""):
        posted.append({"path": path, "body": body, "capability": capability})
        return 200, {"ok": True}

    monkeypatch.setattr(dispatch, "_post_json", _fake_post)
    dispatch.ensure_seat_model_pinned("http://127.0.0.1:11435", "vendor/seat-model", capability)
    dispatch.restore_pin("http://127.0.0.1:11435", "vendor/operator-model", capability)
    assert [p["path"] for p in posted] == ["/api/cloud/model", "/api/cloud/model"]
    assert all(p["capability"] == capability for p in posted), (
        "a council write went out without the run's capability and would be refused by its "
        "own fence: " + repr(posted)
    )


def test_a_leaked_capability_from_a_remote_caller_cannot_write_the_pin(parked_council, monkeypatch):
    from core.council import pin_lock

    calls = _set_cloud_model_spy(monkeypatch)
    status, payload = _post(
        "/api/cloud/model", {"model": "vendor/other-model"}, host="203.0.113.9",
        headers={"X-Vool-Council-Dispatch": pin_lock.dispatch_capability_for_tests()},
    )
    assert status == 403, payload
    assert calls == [], "a remote caller rewrote this machine's model pin"


def test_the_mutation_authority_itself_refuses_a_council_pin_it_was_not_given_the_key_for(
    parked_council,
):
    """Belt and braces at the ONE place every writer converges. The chat command and the
    NL switch intent reach `set_cloud_model` without passing this endpoint at all."""
    from core.cloud_model_control import set_cloud_model

    ok, message, chosen = set_cloud_model("vendor/other-model", owner_local=True)
    assert ok is False, "a direct writer walked past the council fence"
    assert "council" in message.lower()
    assert chosen == ""


def test_a_council_arriving_mid_classification_refuses_the_write_before_it_mutates(monkeypatch):
    """Race arm one, deterministic. The endpoint's pricing classification is slow enough
    for a council to take the pin inside it. The write must then be refused BEFORE the
    mutation — a check done only at the top of the handler would already have passed."""
    from core.council import pin_lock

    in_classification = threading.Event()
    finish_classification = threading.Event()
    mutations: list[dict] = []

    def _slow_classify(*args, **kwargs):
        in_classification.set()
        finish_classification.wait(15)
        return {"cost_state": "free", "reason_code": "", "model": "vendor/racer"}

    def _record(model_or_keyword, *, provider=None, owner_local=False, confirm_paid=False, **kw):
        mutations.append({"model": model_or_keyword, "pin": pin_lock.snapshot()})
        return True, "switched", str(model_or_keyword)

    monkeypatch.setattr("core.cloud_model_control.classify_cloud_model_cost", _slow_classify)
    monkeypatch.setattr("core.cloud_model_control.set_cloud_model", _record)
    monkeypatch.setattr("core.council.api.live_seat_turn_factory",
                        lambda base_url, *a, **k: (lambda *args: {"text": "DIAGNOSIS: x"}))

    outcome: list[tuple[int, dict]] = []

    def _writer():
        outcome.append(_post("/api/cloud/model", {"model": "vendor/racer"}))

    writer = threading.Thread(target=_writer)
    writer.start()
    assert in_classification.wait(10), "the write never reached classification"
    # Nothing is admitted yet — the write is still classifying — so the council takes the
    # pin cleanly. The write then wakes up into a machine it may no longer touch.
    convene_status, convene_payload = _convene(
        seats=[{"role_id": "builder", "model": "vendor/a", "votes": True}])
    assert convene_status == 200, convene_payload
    finish_classification.set()
    writer.join(20)

    assert outcome, "the writer never returned"
    status, payload = outcome[0]
    assert status == 409, payload
    assert payload["error"] == "council_model_pin_active"
    assert mutations == [], (
        "the write mutated the pin after a council had already taken it: " + repr(mutations)
    )
    deadline = time.time() + 25
    while time.time() < deadline and pin_lock.snapshot() is not None:
        time.sleep(0.02)


def test_convene_waits_for_a_model_write_already_inside_the_mutation(monkeypatch):
    """Race arm two, deterministic. A write that WAS admitted holds a real admission, so
    convene drains it rather than pinning through it. Measured by holding the write inside
    `set_cloud_model` and checking that nothing owns the pin for as long as it is there."""
    from core.council import pin_lock

    in_mutation = threading.Event()
    finish_mutation = threading.Event()
    seen_pin: list[dict | None] = []

    def _blocking_set(model_or_keyword, *, provider=None, owner_local=False, confirm_paid=False, **kw):
        in_mutation.set()
        finish_mutation.wait(15)
        seen_pin.append(pin_lock.snapshot())
        return True, "switched", str(model_or_keyword)

    monkeypatch.setattr("core.cloud_model_control.set_cloud_model", _blocking_set)
    monkeypatch.setattr("core.council.api.live_seat_turn_factory",
                        lambda base_url, *a, **k: (lambda *args: {"text": "DIAGNOSIS: x"}))

    write_result: list[tuple[int, dict]] = []
    convene_result: list[tuple[int, dict]] = []

    def _writer():
        write_result.append(_post("/api/cloud/model", {"model": "default"}))

    def _convener():
        convene_result.append(_convene(
            seats=[{"role_id": "builder", "model": "vendor/a", "votes": True}]))

    writer = threading.Thread(target=_writer)
    writer.start()
    assert in_mutation.wait(10), "the write never reached the mutation"
    convener = threading.Thread(target=_convener)
    convener.start()
    # The property, waited on rather than sampled. While the write sits inside the
    # mutation, convene is BLOCKED on the drain, so it must not have returned at all —
    # an earlier version watched the pin for 240ms instead, which made the detector a
    # race the machine could win under load and let the mutation survive the matrix.
    convener.join(2.0)
    assert convene_result == [], (
        "convene returned while a model write was still inside the mutation, so it did "
        "not wait for the drain: " + repr(convene_result)
    )
    assert pin_lock.snapshot() is None, (
        "a council took the pin while a model write was mid-mutation"
    )
    finish_mutation.set()
    writer.join(20)
    convener.join(30)

    assert write_result and write_result[0][0] == 200, write_result
    assert seen_pin == [None], (
        "the write finished under a council's ownership: " + repr(seen_pin)
    )
    assert convene_result and convene_result[0][0] in (200, 409), convene_result
    deadline = time.time() + 25
    while time.time() < deadline and pin_lock.snapshot() is not None:
        time.sleep(0.02)


def test_the_second_writer_saving_a_cloud_key_cannot_reset_the_pin_under_a_council(
    parked_council, monkeypatch
):
    """Writer inventory, second entry: the cloud path of /api/settings/credentials rewrites
    the escalation policy's `model` field on a provider switch — the same composer pin, via
    a door that never touches /api/cloud/model."""
    saved: list[str] = []
    monkeypatch.setattr("core.credential_store.store_credential",
                        lambda *a, **kw: saved.append(a[0] if a else "") or True)
    status, payload = _post(
        "/api/settings/credentials",
        {"name": "llm.cloud.openrouter", "value": "sk-test-key", "provider": "openrouter"},
    )
    assert status == 409, payload
    assert payload["error"] == "council_model_pin_active"
    assert saved == [], "a credential save proceeded and would have rewritten the pin"


def test_a_search_key_save_is_untouched_by_the_pin_fence(parked_council, monkeypatch):
    """The same endpoint stores web-search keys, which write no policy and touch no pin.
    Fencing them would be collateral damage, not safety."""
    from core.search_providers import all_slots as _search_slots

    slots = list(_search_slots())
    if not slots:
        pytest.skip("no search provider slots in this build")
    stored: list[str] = []
    monkeypatch.setattr("core.credential_store.store_credential",
                        lambda *a, **kw: stored.append(a[0] if a else "") or True)
    status, payload = _post("/api/settings/credentials", {"name": slots[0], "value": "brave-key"})
    assert status == 200, payload
    assert stored == [slots[0]], "a search-key save was blocked by a pin it never touches"


def test_the_auto_fallback_preference_is_not_the_composer_pin_and_stays_open(parked_council):
    """`set_auto_free_model` writes `auto_free_model`, never `model`. Conflating the two
    would fence a setting that cannot move the pin — the goal is a fence, not a freeze."""
    status, payload = _post("/api/cloud/auto-model", {"model": "auto"})
    assert status != 404, "the route moved — this test would pass vacuously"
    assert "error" not in payload or payload.get("error") != "council_model_pin_active", (
        "the Auto fallback was conflated with the composer pin: " + str(payload)
    )
    assert status in (200, 400), payload


def test_a_terminal_run_restores_the_operator_pin_carrying_its_own_capability(monkeypatch):
    """The restoration happens while the run still OWNS the fence, so it must present the
    run's capability or the council's own last write is refused by its own guard — and the
    operator's pin never comes back."""
    from core.council import pin_lock

    seen: list[dict] = []

    def _restore(base_url, model, capability=""):
        seen.append({
            "model": model,
            "valid": pin_lock.is_dispatch_capability(capability, owner_local=True),
        })

    monkeypatch.setattr("core.council.api.read_current_pin", lambda base_url: "vendor/operator-pin")
    monkeypatch.setattr("core.council.api.restore_pin", _restore)
    monkeypatch.setattr("core.council.api.live_seat_turn_factory",
                        lambda base_url, *a, **k: (
                            lambda seat, prompt, round_no, run_id: {
                                "text": "DIAGNOSIS: x" if round_no == 1 else "VERDICT: AGREE"}))
    status, payload = _convene(seats=[{"role_id": "builder", "model": "vendor/a", "votes": True}])
    assert status == 200, payload
    deadline = time.time() + 25
    while time.time() < deadline and pin_lock.snapshot() is not None:
        time.sleep(0.02)
    assert pin_lock.snapshot() is None
    assert len(seen) == 1, seen
    assert seen[0]["model"] == "vendor/operator-pin"
    assert seen[0]["valid"] is True, (
        "the operator's pin was restored without the run's capability, so the council's own "
        "fence would have refused it"
    )
