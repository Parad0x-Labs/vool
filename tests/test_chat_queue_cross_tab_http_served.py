"""The one-slot-per-chat queue law against the served daemon, without a browser.

The browser lane (``tests/test_chat_queue_cross_tab_served.py``) proves the page's half of the
cross-tab contract. This file proves the SERVER's half through the same doors the page uses --
the chat turn POST, the queue door's five ops and its read -- with two plain HTTP clients
standing in for two tabs. Everything here runs without playwright, so the law is exercised on
machines where the browser lane must skip.

Same scenarios the delivery review froze, plus the port's own obligations: exactly-once across
two clients, the refused claim returning as its original row, the restart leaving no stuck
slot, a private-chat turn queued from a second tab staying unsaved, and the red-team set
(forged slot ids, cross-chat claims, a client that never releases, the 409 body's contents).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import _reader_served_rig as rig
from tests.test_chat_queue_cross_tab_served import (
    M1,
    M1_ANSWER,
    M2,
    M2_ANSWER,
    _GatedProvider,
    _wait_for,
)

SECRET_OF_CHAT_A = "the harbor combination is 4417"


def _sid(name: str) -> str:
    """A CANONICAL chat id (``openclaw:<20 hex>``), exactly like the page's mintSessionId.

    The chat door keeps a canonical id verbatim (owner-local resume); anything else is folded
    to a different id, which would split the turn's slot truth from the queue's session."""
    return "openclaw:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:20]


def _queue_state(daemon, session_id: str) -> dict:
    with urlopen(daemon.base_url + "/api/chat/queue?session=" + session_id, timeout=3) as response:
        return json.load(response)


def _queue_post(daemon, body: dict) -> dict:
    """One queue-door op, exactly as the page sends it (owner-local POST)."""
    request = Request(
        daemon.base_url + "/api/chat/queue",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _turn_post(
    daemon, message: str, session_id: str, *, turn_id: str, queue_item_id: str = "", timeout: float = 120.0, page: bool = True
):
    """A chat turn POST shaped like the page's run-turn request (``page=False``: the same request
    from a caller that does not ask for the typed busy refusal). Returns (status, body);

    a refusal (409 and friends) comes back as data instead of raising."""
    payload = {
        "messages": [{"role": "user", "content": message}],
        "stream": False,
        "session_id": session_id,
        "model": daemon.model,
        "mode": "auto",
        "turn_id": turn_id,
    }
    if page:
        payload["on_busy"] = "refuse"
    if queue_item_id:
        payload["queue_item_id"] = queue_item_id
    request = Request(
        daemon.base_url + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except ValueError:
            return exc.code, {"raw": raw}


def _calls_for(provider, message: str) -> list[dict]:
    """Provider calls that carried THIS message's words and were not a memory helper."""
    return [
        call
        for call in provider.generation_calls()
        if message in str(call.get("text") or "")
    ]


def _hold_turn(daemon, message: str, session_id: str, turn_id: str, queue_item_id: str = ""):
    """Start a buffered turn in the background; returns the thread and a result box."""
    box: dict = {}

    def run() -> None:
        box["result"] = _turn_post(
            daemon, message, session_id, turn_id=turn_id, queue_item_id=queue_item_id, timeout=180.0
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def _queue_payload_rows(home: Path, session_id: str) -> dict:
    """Verification read of the durable queue rows straight from the home's store."""
    import sqlite3

    rows: dict = {}
    for db_path in sorted(Path(home).rglob("*.db*")):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            for row in conn.execute(
                "SELECT queue_item_id, status, payload_json, idempotency_key, seq FROM message_queue WHERE session_id = ?",
                (session_id,),
            ):
                rows[str(row[0])] = {"status": row[1], "payload": json.loads(row[2] or "{}"), "idem": row[3], "seq": row[4]}
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return rows


def _rig(tmp_path, monkeypatch, *, env_extra: dict | None = None, hold_texts: tuple[str, ...] = (M1,)):
    answers = {M1: M1_ANSWER, M2: M2_ANSWER, "Hi": "ACK-GREETING.", SECRET_OF_CHAT_A: "ACK-SECRET."}
    provider = _GatedProvider(answers, hold_texts=set(hold_texts))
    home = tmp_path / "home"
    daemon = rig.ServedDaemon(
        home,
        provider=provider,
        env_extra={
            "VOOL_INSTALL_PROFILE": "hybrid-fallback",
            "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:1",
            **(env_extra or {}),
        },
    )
    return provider, home, daemon


@pytest.fixture
def http_rig(tmp_path, monkeypatch):
    """A certified daemon with a gated provider (M1 held at the provider)."""
    provider, home, daemon = _rig(tmp_path, monkeypatch)
    with provider:
        daemon.start(timeout=120)
        try:
            daemon.certify(timeout=60)
            yield provider, daemon
        finally:
            daemon.stop()


@pytest.fixture
def http_rig_short_lease(tmp_path, monkeypatch):
    """The same rig with a one-second admission lease, so lease windows are reachable."""
    provider, home, daemon = _rig(
        tmp_path, monkeypatch, env_extra={"VOOL_ADMISSION_LEASE_SECONDS": "1"}, hold_texts=(M1,)
    )
    with provider:
        daemon.start(timeout=120)
        try:
            daemon.certify(timeout=60)
            yield provider, daemon
        finally:
            daemon.stop()


# ---- Proofs: the port's four obligations ----------------------------------------------


def test_two_http_clients_one_chat_run_exactly_once(http_rig):
    provider, daemon = http_rig
    session_id = _sid("http-two-clients")

    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-a-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "first client's turn never reached the provider")
        _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active") is True, 10, "slot never read busy")

        # The second client's DIRECT send into the same chat: refused with the typed 409 --
        # never a second concurrent turn on one session.
        status, body = _turn_post(daemon, M2, session_id, turn_id="t-b-1")
        assert status == 409, (status, body)
        assert body.get("error") == "chat_turn_active"
        assert not _calls_for(provider, M2), "a second concurrent turn reached the provider"

        # The page's reroute for that refusal: the SAME durable queue the busy-branch uses.
        queued = _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M2, "idempotency_key": "reroute-t-b-1"})
        assert queued.get("item"), queued
        assert _wait_for(
            lambda: [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"],
            10,
            "second client's message never appeared in the queue",
        )
    finally:
        provider.release(M1)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]

    # The queued message executes exactly once without a second manual send (the heartbeat's
    # pump role is played here by the explicit claim a page would make).
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 30, "slot never freed after the first turn")
    claimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "http-tab-b", "turn_id": "t-b-2"})
    item = claimed.get("item")
    assert item and item["payload"]["text"] == M2, claimed
    status, body = _turn_post(daemon, M2, session_id, turn_id="t-b-2", queue_item_id=item["queue_item_id"])
    assert status == 200, (status, body)
    _queue_post(daemon, {"op": "complete", "session_id": session_id, "queue_item_id": item["queue_item_id"]})

    _wait_for(lambda: len(_calls_for(provider, M2)) >= 1, 45, "the queued message never executed")
    time.sleep(0.5)
    assert len(_calls_for(provider, M2)) == 1, f"queued message ran more than once: {len(_calls_for(provider, M2))}"
    assert _wait_for(lambda: not _queue_state(daemon, session_id).get("queue"), 10, "queue did not drain")


def test_the_brake_is_answered_while_a_turn_runs_and_takes_no_slot(http_rig):
    """The emergency brake is never refused or queued behind a running turn: it is answered at
    once, beside the turn, and leaves the chat's slot exactly as it found it."""
    provider, daemon = http_rig
    session_id = _sid("http-brake")

    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-brake-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the held turn never reached the provider")
        _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active") is True, 10, "slot never read busy")

        status, body = _turn_post(daemon, "/stopx402", session_id, turn_id="t-brake-2", timeout=30.0)
        assert status == 200, (status, body)
        assert "Spending is now frozen" in json.dumps(body), body
        assert not _queue_state(daemon, session_id).get("queue"), "the brake was queued"
        # The running turn still holds the slot: the brake neither took it nor freed it.
        assert _queue_state(daemon, session_id).get("turn_active") is True
    finally:
        provider.release(M1)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 30, "slot never freed after the turn")


def test_a_caller_that_does_not_ask_for_the_refusal_waits_and_runs_once(http_rig):
    """A caller that is not the page (a script on this machine, another app's runner) keeps the
    session gate's contract: its turn waits behind the running one, then runs exactly once --
    no typed refusal it was never written to handle, no overtaking, and no slot left held."""
    provider, daemon = http_rig
    session_id = _sid("http-plain-caller")

    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-plain-1")
    waiter: dict = {}
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the held turn never reached the provider")
        _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active") is True, 10, "slot never read busy")

        second = threading.Thread(
            target=lambda: waiter.setdefault(
                "result", _turn_post(daemon, M2, session_id, turn_id="t-plain-2", timeout=180.0, page=False)
            ),
            daemon=True,
        )
        second.start()
        second.join(3.0)
        assert second.is_alive(), f"the plain caller was answered while the turn ran: {waiter.get('result')}"
        assert not _calls_for(provider, M2), "the plain caller overtook the running turn"
    finally:
        provider.release(M1)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]
    second.join(timeout=120)
    assert not second.is_alive(), "the plain caller never ran after the turn ended"
    assert waiter["result"][0] == 200, waiter["result"]
    time.sleep(0.5)
    assert len(_calls_for(provider, M2)) == 1, f"the plain caller's message ran {len(_calls_for(provider, M2))} times"
    assert not _queue_state(daemon, session_id).get("queue"), "the plain caller's message was queued"
    # Nothing it did holds the slot: the chat reads free, and the page's next send runs.
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 30, "slot left held")
    status, body = _turn_post(daemon, "Hi", session_id, turn_id="t-plain-3", timeout=60.0)
    assert status == 200, (status, body)


def test_a_refused_claim_comes_back_as_its_original_row(http_rig_short_lease):
    provider, daemon = http_rig_short_lease
    session_id = _sid("http-refused-claim")

    # Tab B's message is claimed (admission holds for the claim) but never dispatched; its
    # reservation lapses with the one-second lease while the row stays in_flight.
    enqueued = _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M2, "idempotency_key": "orig-http-1"})
    qid = enqueued["item"]["queue_item_id"]
    claimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "http-tab-b"})
    assert claimed["item"]["queue_item_id"] == qid, claimed
    before = claimed["item"]

    time.sleep(1.4)  # the claim's admission lapses; the row stays in_flight (recovery's owner)

    # A direct turn takes the now-free slot and holds it at the provider (a real racing send).
    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-race-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the racing turn never reached the provider")
        _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active") is True, 10, "racing turn never held the slot")

        # The stale claim's dispatch is refused -- it cannot jump past the live turn.
        status, body = _turn_post(daemon, M2, session_id, turn_id="t-b-1", queue_item_id=qid)
        assert status == 409 and body.get("error") == "chat_turn_active", (status, body)

        # The page's release: the ORIGINAL row returns to pending -- same identity, same FIFO
        # position, same idempotency key; no duplicate at the tail.
        released = _queue_post(daemon, {"op": "release", "session_id": session_id, "queue_item_id": qid})
        assert released.get("released") is True, released
        after = released["item"]
        assert after["queue_item_id"] == qid
        assert after["status"] == "pending"
        assert after["seq"] == before["seq"]
        assert after.get("idempotency_key") == before.get("idempotency_key")
    finally:
        provider.release(M1)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]

    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 30, "slot never freed")
    rows = _queue_state(daemon, session_id).get("queue") or []
    assert [r["payload"]["text"] for r in rows] == [M2], rows  # one row, original position, no copy


def test_stop_vool_during_a_queued_turn_leaves_no_stuck_slot_after_restart(tmp_path, monkeypatch):
    provider, home, daemon = _rig(tmp_path, monkeypatch)
    session_id = _sid("http-restart")
    # One provider context spans BOTH daemons (a context exit would end its thread for good).
    with provider:
        daemon.start(timeout=120)
        try:
            daemon.certify(timeout=60)

            # A claimed, dispatched message is executing (held at the provider) when VOOL stops.
            _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M1, "idempotency_key": "restart-1"})
            _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M2, "idempotency_key": "restart-2"})
            claimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "http-tab-a"})
            qid = claimed["item"]["queue_item_id"]
            assert claimed["item"]["payload"]["text"] == M1, claimed
            thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-boot-1", queue_item_id=qid)
            _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the dispatched message never reached the provider")
            _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active") is True, 10, "turn never held the slot")

            daemon.stop()  # Stop VOOL mid-turn; the buffered POST dies with the process
            thread.join(timeout=30)
        except BaseException:
            daemon.stop()
            raise

        # A fresh daemon on the SAME home: startup recovery resets interrupted rows and the
        # admission registry starts empty -- no stuck slot survives the restart.
        daemon2 = rig.ServedDaemon(
            home,
            provider=provider,
            env_extra={
                "VOOL_INSTALL_PROFILE": "hybrid-fallback",
                "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:1",
            },
        )
        daemon2.start(timeout=120)
        try:
            state = _queue_state(daemon2, session_id)
            assert state.get("turn_active") is False, "the restart left a stuck slot"
            statuses = {row["payload"]["text"]: row["status"] for row in state.get("queue") or []}
            assert statuses.get(M1) == "pending", f"interrupted row was not reset: {statuses}"
            assert statuses.get(M2) == "pending", statuses

            # The chat is genuinely usable: the recovered message is claimed and dispatched.
            reclaimed = _queue_post(daemon2, {"op": "claim", "session_id": session_id, "lease_owner": "http-tab-a"})
            assert reclaimed["item"]["queue_item_id"] == qid, reclaimed  # FIFO survived
            provider.release(M1)  # the held call answers on the NEW dispatch
            status, body = _turn_post(daemon2, M1, session_id, turn_id="t-boot-2", queue_item_id=qid)
            assert status == 200, (status, body)
            _queue_post(daemon2, {"op": "complete", "session_id": session_id, "queue_item_id": qid})
            _wait_for(lambda: len(_calls_for(provider, M1)) >= 2, 45, "the recovered message never re-ran")
        finally:
            provider.release(M1)
            daemon2.stop()


def test_a_private_chat_turn_queued_from_a_second_tab_is_still_unsaved(http_rig):
    provider, daemon = http_rig
    session_id = _sid("http-private")

    # The chat turns private at the door, through the real phrase lane.
    status, body = _turn_post(daemon, "let's go private", session_id, turn_id="t-priv-0")
    assert status == 200, (status, body)
    with urlopen(daemon.base_url + "/api/chat/history?session=" + session_id, timeout=10) as response:
        assert json.load(response).get("private_chat") is True, "the chat never became private"

    # A private turn is live in tab A; tab B's direct send is refused and its message is
    # queued (the cross-tab law) -- and must stay UNSAVED because the chat is private.
    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-priv-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "private turn never reached the provider")
        status, body = _turn_post(daemon, M2, session_id, turn_id="t-priv-2")
        assert status == 409 and body.get("error") == "chat_turn_active", (status, body)
        queued = _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M2, "idempotency_key": "priv-1"})
        assert queued.get("item"), queued
    finally:
        provider.release(M1)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]

    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 30, "slot never freed")
    claimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "http-tab-b"})
    qid = claimed["item"]["queue_item_id"]
    status, body = _turn_post(daemon, M2, session_id, turn_id="t-priv-2", queue_item_id=qid)
    assert status == 200, (status, body)
    _queue_post(daemon, {"op": "complete", "session_id": session_id, "queue_item_id": qid})
    _wait_for(lambda: len(_calls_for(provider, M2)) >= 1, 45, "the queued private message never executed")

    # Unsaved: the transcript never gained the words, and the completed row's payload is blank.
    with urlopen(daemon.base_url + "/api/chat/history?session=" + session_id, timeout=10) as response:
        transcript = json.dumps(json.load(response))
    assert M2 not in transcript and M2_ANSWER not in transcript, "a private queued turn was saved"
    rows = _queue_payload_rows(Path(daemon.home), session_id)
    assert rows.get(qid, {}).get("payload") == {}, f"private queued words survived at rest: {rows.get(qid)}"


# ---- Red team --------------------------------------------------------------------------


def test_forged_slot_ids_cannot_steal_the_admission(http_rig):
    provider, daemon = http_rig
    session_id = _sid("http-forged")

    # A real turn holds the slot; three forged presenters try to start past it.
    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-real-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the real turn never reached the provider")
        victim = _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M2, "idempotency_key": "forged-1"})

        # 1. Someone else's queue_item_id presented as a claim dispatch.
        status, body = _turn_post(daemon, "let me through", session_id, turn_id="t-forge-1", queue_item_id=victim["item"]["queue_item_id"])
        assert status == 409 and body.get("error") == "chat_turn_active", (status, body)
        # 2. An invented id.
        status, body = _turn_post(daemon, "let me through", session_id, turn_id="t-forge-2", queue_item_id="msg-doesnotexist")
        assert status == 409 and body.get("error") == "chat_turn_active", (status, body)
        # 3. No id at all (a plain racing direct send).
        status, body = _turn_post(daemon, "let me through", session_id, turn_id="t-forge-3")
        assert status == 409 and body.get("error") == "chat_turn_active", (status, body)
        assert not _calls_for(provider, "let me through"), "a forged presenter executed a turn"
    finally:
        provider.release(M1)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]

    # One claim, two dispatch POSTs presenting it: exactly one executes, the other is refused.
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 30, "slot never freed")
    claimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "http-forge"})
    qid = claimed["item"]["queue_item_id"]
    first = threading.Thread(target=lambda: box.setdefault("first", _turn_post(daemon, M2, session_id, turn_id="t-dup-1", queue_item_id=qid)), daemon=True)
    second = threading.Thread(target=lambda: box.setdefault("second", _turn_post(daemon, M2, session_id, turn_id="t-dup-2", queue_item_id=qid)), daemon=True)
    first.start(); second.start(); first.join(timeout=90); second.join(timeout=90)
    statuses = sorted([box["first"][0], box["second"][0]])
    assert statuses == [200, 409], box
    _wait_for(lambda: len(_calls_for(provider, M2)) >= 1, 45, "the claim's message never executed")
    time.sleep(0.5)
    assert len(_calls_for(provider, M2)) == 1, "the same claim executed twice"


def test_cross_chat_claims_and_releases_are_refused(http_rig):
    provider, daemon = http_rig
    chat_a = _sid("http-chat-a")
    chat_b = _sid("http-chat-b")

    thread, box = _hold_turn(daemon, SECRET_OF_CHAT_A, chat_a, turn_id="t-a-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, SECRET_OF_CHAT_A)) >= 1, 45, "chat A's turn never reached the provider")
        # Chat B is fully usable while A is busy: enqueue, claim, and the slot read agree.
        enqueued = _queue_post(daemon, {"op": "enqueue", "session_id": chat_b, "text": M2, "idempotency_key": "cross-1"})
        assert enqueued.get("item"), enqueued
        assert _queue_state(daemon, chat_b).get("turn_active") is False
        claimed = _queue_post(daemon, {"op": "claim", "session_id": chat_b, "lease_owner": "http-tab-b"})
        qid_b = claimed["item"]["queue_item_id"]

        # A release for B's item presented under A's session is refused -- and answers
        # WITHOUT the item's payload.
        refused = _queue_post(daemon, {"op": "release", "session_id": chat_a, "queue_item_id": qid_b})
        assert refused.get("released") is False and refused.get("item") is None, refused
        # A claim naming A's session gets A's truth (busy), never B's item.
        a_claim = _queue_post(daemon, {"op": "claim", "session_id": chat_a})
        assert a_claim.get("item") is None and a_claim.get("turn_active") is True, a_claim
    finally:
        provider.release(SECRET_OF_CHAT_A)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]

    # B's row is untouched by every foreign attempt above.
    rows = _queue_state(daemon, chat_b).get("queue") or []
    assert [r["queue_item_id"] for r in rows if r["status"] == "in_flight"] == [qid_b], rows


def test_a_client_that_never_releases_is_lease_bounded(http_rig_short_lease):
    provider, daemon = http_rig_short_lease
    session_id = _sid("http-lease")

    # The client claims and disappears: no dispatch, no release, no complete.
    _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M2, "idempotency_key": "lease-1"})
    claimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "http-gone"})
    qid = claimed["item"]["queue_item_id"]
    _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active") is True, 10, "claim never held the slot")

    # The reservation is bounded: past the one-second lease the slot reads free and a new
    # turn is admitted (the abandoned row stays in_flight -- startup recovery's owner).
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 15, "the abandoned claim held the slot past its lease")
    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-after-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the post-lease turn never reached the provider")
        # The disappeared client's late dispatch still cannot jump past the live turn.
        status, body = _turn_post(daemon, M2, session_id, turn_id="t-gone-1", queue_item_id=qid)
        assert status == 409 and body.get("error") == "chat_turn_active", (status, body)
    finally:
        provider.release(M1)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]

    # Recovery for the stranded row is the release a page would send: the original returns
    # to pending and runs once, still exactly once.
    released = _queue_post(daemon, {"op": "release", "session_id": session_id, "queue_item_id": qid})
    assert released.get("released") is True and released["item"]["queue_item_id"] == qid, released
    reclaimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "http-back"})
    assert reclaimed["item"]["queue_item_id"] == qid, reclaimed
    status, body = _turn_post(daemon, M2, session_id, turn_id="t-back-1", queue_item_id=qid)
    assert status == 200, (status, body)
    _queue_post(daemon, {"op": "complete", "session_id": session_id, "queue_item_id": qid})
    _wait_for(lambda: len(_calls_for(provider, M2)) >= 1, 45, "the recovered message never executed")
    time.sleep(0.5)
    assert len(_calls_for(provider, M2)) == 1, "the recovered message ran more than once"


def test_the_409_body_leaks_nothing_of_the_busy_turn(http_rig):
    provider, daemon = http_rig
    chat_a = _sid("http-secret-a")
    chat_b = _sid("http-plain-b")

    thread, box = _hold_turn(daemon, SECRET_OF_CHAT_A, chat_a, turn_id="t-a-1")
    try:
        _wait_for(lambda: len(_calls_for(provider, SECRET_OF_CHAT_A)) >= 1, 45, "chat A's turn never reached the provider")
        # The refused sender is the SAME chat's second tab: the only request the 409 answers.
        status, body = _turn_post(daemon, M2, chat_a, turn_id="t-a-2")
        assert status == 409, (status, body)
        # The refusal names the law and nothing else: neither the busy turn's words nor any
        # queue item, turn id or session beyond the fixed error and sentence.
        assert body == {
            "error": "chat_turn_active",
            "detail": "This chat is already running a turn. The message was not started.",
        }, body
        assert SECRET_OF_CHAT_A not in json.dumps(body) and M2 not in json.dumps(body)

        # The queue read is per session: B sees only B's (empty) queue and B's slot truth,
        # and A's read carries no message text while the item is only pending.
        state_b = _queue_state(daemon, chat_b)
        assert state_b.get("queue") == [] and state_b.get("turn_active") is False, state_b
        assert SECRET_OF_CHAT_A not in json.dumps(state_b)
        state_a = _queue_state(daemon, chat_a)
        assert state_a.get("turn_active") is True, state_a
        assert SECRET_OF_CHAT_A not in json.dumps(state_a)
    finally:
        provider.release(SECRET_OF_CHAT_A)
        thread.join(timeout=90)
    assert box["result"][0] == 200, box["result"]


# ---- The open-is-a-wakeup law, measured where the latency lives ------------------------
#
# The rescue's own browser test pins the provider call inside 4 seconds of the sidebar click.
# On a loaded machine the turn pipeline itself (the dead-endpoint fallback ladder plus the
# memory helpers around a turn) can take longer than that while the OPEN's pump did its job
# in well under a second -- the item is claimed and the run is active. This variant pins the
# law (the OPEN pumps; the heartbeat is neutralized) at the queue boundary and gives the
# provider arrival the same 45s budget every other served test uses.


def test_opening_a_chat_pumps_its_pending_queue_robust(tmp_path, monkeypatch):
    from tests import served_browser

    provider, home, daemon = _rig(tmp_path, monkeypatch)
    with provider:
        daemon.start(timeout=120)
        try:
            daemon.certify(timeout=60)
        except Exception:
            daemon.stop()
            raise
        try:
            manager, browser = served_browser.launch_chromium()
        except Exception as exc:  # pragma: no cover - availability differs per machine
            daemon.stop()
            pytest.skip(f"served browser unavailable here: {exc}")
        try:
            context = browser.new_context()
            tab_a = context.new_page()
            errors: list[str] = []
            tab_a.on("pageerror", lambda error: errors.append(str(error)))
            tab_a.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
            tab_a.wait_for_selector("#input", timeout=5000)
            _wait_for(lambda: tab_a.evaluate("displayedChat"), 5, "first tab never derived its chat")
            session_id = tab_a.evaluate("displayedChat")
            tab_b = context.new_page()
            tab_b.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
            tab_b.wait_for_selector("#input", timeout=5000)
            _wait_for(lambda: tab_b.evaluate("displayedChat") == session_id, 5, "tabs differ")

            # A held turn in tab A; a third page queues M2 behind it, then every knowing tab
            # closes. Tab B sits on a different chat with the heartbeat sweep neutralized:
            # the OPEN must be the only wakeup left.
            tab_a.locator("#input").fill(M1)
            tab_a.locator("#send").click()
            _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "held turn never reached the provider")
            tab_c = context.new_page()
            tab_c.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
            _wait_for(lambda: tab_c.evaluate("displayedChat") == session_id, 10, "third page never settled")
            tab_c.locator("#input").fill(M2)
            tab_c.locator("#send").click()
            _wait_for(
                lambda: [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"],
                15,
                "M2 never queued",
            )
            tab_c.close()
            tab_a.close()
            provider.release(M1)
            _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "worker never finished")

            tab_b.locator("#newChat").click()
            _wait_for(lambda: tab_b.evaluate("displayedChat") != session_id, 5, "tab B never opened a fresh chat")
            tab_b.evaluate("lastQueueItemsByChat.delete(displayedChat)")
            tab_b.evaluate("() => { window.savedSweep = pumpKnownQueuedChats; pumpKnownQueuedChats = function() {}; }")

            # THE LAW: the sidebar click's open claims the pending item at once -- the queue
            # boundary is the open's own act, independent of turn-pipeline latency.
            clicked = time.monotonic()
            tab_b.locator(".session:not(.active) .s-title").first.click(timeout=5000)
            _wait_for(
                lambda: not [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"],
                4.0,
                "the open never claimed the pending message",
            )
            claimed_elapsed = time.monotonic() - clicked

            # And the claimed message executes through the real lane exactly once.
            _wait_for(lambda: len(_calls_for(provider, M2)) >= 1, 45, "the opened chat's message never executed")
            time.sleep(0.5)
            assert len(_calls_for(provider, M2)) == 1, "the opened chat's message ran more than once"
            assert claimed_elapsed < 4.0, f"claim itself took {claimed_elapsed:.1f}s"
            assert not errors, errors
        finally:
            try:
                browser.close()
                manager.stop()
            finally:
                daemon.stop()
