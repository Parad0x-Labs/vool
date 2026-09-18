"""C3 — the council's terminal result belongs in the chat it was convened from.

A run that lives only inside a modal is a run the operator loses on reload. This suite
pins the SERVER half of moving it into the chat: a typed assistant-artifact persistence
seam, a compact readable summary carrying a machine-readable final marker, and the one
thing that must never happen — a council summary entering the memory pipeline as if the
operator had said it.

Worst cases, not greetings: a crashed run (no outcome, no rounds), a summary whose text
looks exactly like a user's own words to every extractor downstream, a run with no chat
to belong to, and a double-terminal persist that must leave exactly one message.
"""

from __future__ import annotations

import json

import pytest

from core.runtime_paths import configure_runtime_home

CHAT = "openclaw:c3c3c3c3c3c3c3c3c3c3"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path):
    configure_runtime_home(tmp_path / "runtime-home")
    from core.memory.files import conversation_log_path

    conversation_log_path().parent.mkdir(parents=True, exist_ok=True)
    yield tmp_path


@pytest.fixture()
def isolated_council(tmp_path, monkeypatch):
    from core.council import pin_lock

    pin_lock.reset_on_startup()

    def _patched(*parts):
        base = tmp_path / "council-data"
        out = base
        for part in parts:
            out = out / part
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    yield tmp_path
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


def _state(**over):
    state = {
        "run_id": "council-abc123def456",
        "state": "converged",
        "problem": "the card never reaches the chat",
        "chat_session": CHAT,
        "max_rounds": 5,
        "started_at": 1000.0,
        "seats": [
            {"seat_id": "s1", "role_id": "builder", "model": "vendor/alpha", "votes": True},
            {"seat_id": "s2", "role_id": "falsifier", "model": "vendor/beta", "votes": True},
        ],
        "candidate": "the run state never reached the chat log",
        "candidate_source": "diagnosis_line",
        "rounds": [
            {"round_no": 1, "reports": [
                {"seat_id": "s1", "round_no": 1, "status": "landed", "verdict": None,
                 "receipt_count": 2, "attempts": 1, "retries_used": 0,
                 "model_requested": "vendor/alpha", "model_actual": "vendor/alpha-0125",
                 "model_evidence": "actual_adapter", "text_ref": {"kind": "ledger", "seq": 3}},
                {"seat_id": "s2", "round_no": 1, "status": "landed", "verdict": None,
                 "receipt_count": 0, "attempts": 2, "retries_used": 1,
                 "model_requested": "vendor/beta", "model_actual": None,
                 "model_evidence": "unknown", "text_ref": {"kind": "ledger", "seq": 6}},
            ]},
            {"round_no": 2, "reports": [
                {"seat_id": "s1", "round_no": 2, "status": "landed", "verdict": "AGREE",
                 "receipt_count": 1, "attempts": 1, "retries_used": 0,
                 "model_requested": "vendor/alpha", "model_actual": "vendor/alpha-0125",
                 "model_evidence": "actual_adapter", "text_ref": {"kind": "ledger", "seq": 9}},
                {"seat_id": "s2", "round_no": 2, "status": "landed", "verdict": "AGREE",
                 "receipt_count": 0, "attempts": 1, "retries_used": 0,
                 "model_requested": "vendor/beta", "model_actual": None,
                 "model_evidence": "unknown", "text_ref": {"kind": "ledger", "seq": 11}},
            ]},
        ],
        "outcome": {
            "result": "adjudicated", "agree": 2, "disagree": 0, "rounds": 2,
            "candidate": "the run state never reached the chat log",
            "authority_note": "adjudication only — promotion, merge, and spend remain with the operator",
        },
    }
    state.update(over)
    return state


# ----------------------------------------------------------------- the marker


def test_marker_is_the_exact_agreed_wire_format_and_round_trips():
    from core.council.chat_record import council_marker, parse_council_marker

    line = council_marker(run_id="council-abc123def456", state="converged", round_no=2, max_rounds=5)
    assert line == "council | run=council-abc123def456 | converged | r2/5"
    parsed = parse_council_marker("Council adjudicated.\n\n" + line)
    assert parsed == {
        "run_id": "council-abc123def456", "state": "converged",
        "round_no": 2, "max_rounds": 5,
    }


def test_prose_that_merely_mentions_a_council_is_not_a_marker():
    """A user writing about the council in a normal message must never be mistaken for
    one. The marker is a whole line in the agreed shape or it is not a marker."""
    from core.council.chat_record import parse_council_marker

    for text in [
        "council | run=x",
        "the council | run=council-1 | converged | r2/5 was fine",   # not line-anchored
        "council | run=council-1 | converged | r2",
        "council | run=council-1 | converged | rX/5",
        "",
    ]:
        assert parse_council_marker(text) is None, text


# -------------------------------------------------------------- the summary


def test_summary_is_readable_plain_text_and_ends_with_the_marker():
    from core.council.chat_record import council_summary_text, parse_council_marker

    text = council_summary_text(_state())
    assert "<" not in text and "{" not in text, "the summary is prose for a plain-text client"
    assert "adjudicated" in text.lower()
    assert "2 agree" in text and "0 disagree" in text
    assert "the run state never reached the chat log" in text, "the candidate must survive"
    assert "promotion, merge and spend" in text or "promotion, merge, and spend" in text
    assert text.strip().splitlines()[-1] == parse_council_marker(text)["run_id"].join(
        ["council | run=", " | converged | r2/5"]
    ), "the marker is the LAST line so a plain-text client ends on it"


def test_a_crashed_run_with_no_outcome_still_produces_a_summary_and_a_marker():
    """The worst case: the thread died before any round landed. There is no outcome, no
    candidate and no round — and the chat must still say what happened."""
    from core.council.chat_record import council_summary_text, parse_council_marker

    text = council_summary_text(_state(
        state="crashed", rounds=[], outcome={}, candidate=None,
        error="RuntimeError: seat transport died",
    ))
    assert "crashed" in text.lower()
    assert "RuntimeError: seat transport died" in text
    parsed = parse_council_marker(text)
    assert parsed["state"] == "crashed" and parsed["round_no"] == 0 and parsed["max_rounds"] == 5


def test_every_seat_is_named_with_role_and_requested_and_actual_kept_apart():
    from core.council.chat_record import council_summary_text

    text = council_summary_text(_state())
    assert "builder" in text and "falsifier" in text
    assert "vendor/alpha-0125" in text, "an established actual model is stated"
    assert "actual model unknown" in text, "a seat with no evidence says so"
    beta_line = next(line for line in text.splitlines() if "falsifier" in line)
    assert "vendor/beta" in beta_line and "actual model unknown" in beta_line
    assert beta_line.count("vendor/beta") == 1, (
        "the requested model must never be echoed a second time as the actual one"
    )


# ------------------------------------------------- the typed persistence seam


def test_artifact_event_lands_in_chat_history_without_a_user_row():
    from core.persistent_memory import append_assistant_artifact_event, recent_conversation_events

    append_assistant_artifact_event(
        session_id=CHAT, text="Council adjudicated.\n\ncouncil | run=r1 | converged | r2/5",
        artifact_kind="council_summary", artifact={"run_id": "r1"},
    )
    rows = recent_conversation_events(CHAT, limit=10, include_artifacts=True)
    assert len(rows) == 1
    assert rows[0]["user"] == "", "an assistant artifact has no user side — inventing one is a lie"
    assert rows[0]["artifact_kind"] == "council_summary"
    assert "council | run=r1" in rows[0]["assistant"]


def test_artifact_rows_are_invisible_to_recall_and_extraction_by_default():
    """The whole reason for a typed seam: the recall/extraction readers must not see it."""
    from core.persistent_memory import (
        append_assistant_artifact_event,
        append_conversation_event,
        recent_conversation_events,
    )

    append_conversation_event(session_id=CHAT, user_input="what broke?", assistant_output="the card")
    append_assistant_artifact_event(
        session_id=CHAT, text="Council adjudicated.\n\ncouncil | run=r1 | converged | r2/5",
        artifact_kind="council_summary", artifact={"run_id": "r1"},
    )
    default_rows = recent_conversation_events(CHAT, limit=10)
    assert [r["user"] for r in default_rows] == ["what broke?"], (
        "the default reader — the one memory recall uses — must not see the artifact"
    )
    assert len(recent_conversation_events(CHAT, limit=10, include_artifacts=True)) == 2


def test_persisting_a_summary_writes_no_fact_no_dialogue_and_no_semantic_turn():
    """Sabotage-proof non-pollution: the artifact seam must touch the transcript and
    NOTHING else. Every mining path is watched, and a call through any of them fails."""
    import core.persistent_memory as pm

    touched: list[str] = []

    for name in (
        "_record_assistant_dialogue_turn", "_auto_capture_memory", "_update_user_heuristics",
        "_detect_implicit_feedback", "_update_session_summary", "refresh_operator_dense_profile",
    ):
        assert hasattr(pm, name), f"guarded mining path {name} vanished — update this test"

    class Tripwire:
        def __init__(self, name):
            self.name = name

        def __call__(self, *args, **kwargs):
            touched.append(self.name)

    for name in (
        "_record_assistant_dialogue_turn", "_auto_capture_memory", "_update_user_heuristics",
        "_detect_implicit_feedback", "_update_session_summary", "refresh_operator_dense_profile",
    ):
        setattr(pm, name, Tripwire(name))
    try:
        pm.append_assistant_artifact_event(
            session_id=CHAT,
            text="Council adjudicated: my name is Bob and I always deploy on Fridays.",
            artifact_kind="council_summary", artifact={"run_id": "r1"},
        )
    finally:
        import importlib

        importlib.reload(pm)
    assert touched == [], f"the artifact seam fed the memory pipeline: {touched}"


def test_history_api_serves_the_artifact_as_an_assistant_message():
    from core.persistent_memory import append_assistant_artifact_event
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    append_assistant_artifact_event(
        session_id=CHAT, text="Council adjudicated.\n\ncouncil | run=r1 | converged | r2/5",
        artifact_kind="council_summary", artifact={"run_id": "r1"},
    )
    response = dispatch_get(
        path="/api/chat/history", query={"session": [CHAT]},
        runtime=RuntimeServices(display_name="VOOL"), model_name="vool",
        client_host="127.0.0.1",
    )
    payload = json.loads(response.body.decode("utf-8"))
    messages = payload["messages"]
    assert [m["role"] for m in messages] == ["assistant"], "no phantom user row on reload"
    assert "council | run=r1 | converged | r2/5" in messages[0]["content"]
    assert messages[0].get("artifact", {}).get("kind") == "council_summary"


# ------------------------------------------------- terminal-state persistence


def test_a_terminal_run_writes_exactly_one_summary_into_its_originating_chat(isolated_council):
    from core.council.chat_record import persist_council_summary
    from core.council.run_store import CouncilRunStore
    from core.persistent_memory import recent_conversation_events

    store = CouncilRunStore("council-abc123def456")
    store.write_state(_state())
    assert persist_council_summary(store.run_id) is True
    # A second terminal persist (crash path AND normal path both firing) must not double up.
    assert persist_council_summary(store.run_id) is False
    rows = recent_conversation_events(CHAT, limit=10, include_artifacts=True)
    assert len(rows) == 1, "one council run leaves exactly one message"
    assert "council | run=council-abc123def456 | converged | r2/5" in rows[0]["assistant"]


def test_a_live_run_is_never_persisted_and_a_chatless_run_has_nowhere_to_go(isolated_council):
    from core.council.chat_record import persist_council_summary
    from core.council.run_store import CouncilRunStore
    from core.persistent_memory import recent_conversation_events

    live = CouncilRunStore("council-live0000")
    live.write_state(_state(run_id="council-live0000", state="round_open"))
    assert persist_council_summary(live.run_id) is False

    orphan = CouncilRunStore("council-orphan00")
    orphan.write_state(_state(run_id="council-orphan00", chat_session=""))
    assert persist_council_summary(orphan.run_id) is False
    assert recent_conversation_events(CHAT, limit=10, include_artifacts=True) == []


def test_the_run_ledger_records_that_the_chat_was_written(isolated_council):
    """The persistence is itself evidence: a run whose summary reached the chat says so
    in its own append-only record, which is also what makes the second call a no-op."""
    from core.council.chat_record import persist_council_summary
    from core.council.run_store import CouncilRunStore

    store = CouncilRunStore("council-abc123def456")
    store.write_state(_state())
    persist_council_summary(store.run_id)
    types = [row["type"] for row in store.read_events_ordered()]
    assert "chat_summary_persisted" in types


# ------------------------------------ the live thread, end to end through the API


def test_a_convened_run_leaves_its_verdict_in_the_chat_transcript(isolated_council, monkeypatch):
    """The whole point, driven through the real endpoint and the real council thread:
    convene from a chat, let the bench finish, and the chat's own transcript holds the
    verdict — reload-survivable, with no fabricated user turn beside it."""
    import time

    from core.persistent_memory import recent_conversation_events
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    monkeypatch.setattr("core.council.api.read_current_pin", lambda base_url: "")
    _restore_pin_double(monkeypatch)

    def _factory(base_url, dispatch_capability=""):
        assert dispatch_capability, "convene built a seat factory with no pin capability"

        def _turn(seat, prompt, round_no, run_id):
            text = (
                ("DIAGNOSIS: the card never reached the chat."
                 if seat.role_id == "builder" else "gathered my own evidence")
                if round_no == 1 else "VERDICT: AGREE"
            )
            return {"text": text, "receipt_count": 1, "session_id": f"stub-{seat.seat_id}"}

        return _turn

    monkeypatch.setattr("core.council.api.live_seat_turn_factory", _factory)

    res = dispatch_post(
        path="/api/council/convene",
        body={
            "problem": "the council result never reaches the chat",
            "chat_session": CHAT,
            "seats": [
                {"role_id": "builder", "model": "vendor/model-a", "votes": True},
                {"role_id": "falsifier", "model": "vendor/model-b", "votes": True},
            ],
        },
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="N"), model_name="vool",
        workspace_root_provider=lambda: "/tmp", client_host="127.0.0.1",
    )
    payload = json.loads(res.body.decode("utf-8"))
    assert payload["ok"], payload
    run_id = payload["run_id"]

    deadline = time.time() + 20
    state = ""
    while time.time() < deadline:
        status_res = dispatch_get(
            path="/api/council/status", query={"run": [run_id]},
            runtime=RuntimeServices(display_name="N"), model_name="vool", client_host="127.0.0.1",
        )
        body = json.loads(status_res.body.decode("utf-8"))
        state = str(body.get("run", {}).get("state") or "")
        if state in {"converged", "failed", "no_convergence", "stopped", "crashed"} and not body["live"]:
            break
        time.sleep(0.05)
    assert state == "converged", f"the stubbed bench must converge; got {state!r}"

    rows = recent_conversation_events(CHAT, limit=10, include_artifacts=True)
    assert len(rows) == 1, "one run, one message"
    assert f"council | run={run_id} | converged | r2/5" in rows[0]["assistant"]
    assert rows[0]["user"] == ""
    assert recent_conversation_events(CHAT, limit=10) == [], (
        "the verdict must stay out of every recall path"
    )


def test_state_carries_a_start_instant_so_elapsed_is_measured_not_guessed(isolated_council):
    """A card that starts its clock when the browser opens reports the age of the TAB.
    Elapsed has to be measured from the run's own start, which means the state has to
    carry one."""
    from core.council.orchestrator import CouncilOrchestrator, Seat

    orchestrator = CouncilOrchestrator(
        problem="p",
        seats=[Seat("s1", "builder", "vendor/a", True)],
        seat_turn=lambda seat, prompt, round_no, run_id: {"text": "DIAGNOSIS: x"},
        chat_session=CHAT,
    )
    orchestrator._persist("convened")
    state = orchestrator.store.read_state()
    assert isinstance(state.get("started_at"), float)
    assert state["started_at"] <= state["updated_at"]
