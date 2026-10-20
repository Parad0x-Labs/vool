"""A queued message's row is finished by the turn that ran it, not by the page that sent it.

The page used to be the only thing that completed a queued row (after its stream ended). A tab
closed or reloaded mid-answer left the row in flight forever: other tabs offered "Send again"
for an answered message, and the next daemon start reset it to pending and ran it a second
time. The served daemon here is real; the two clients stand in for tabs and never send the
page's ``complete`` op unless a test says so.
"""

from __future__ import annotations

import http.client
import json
from urllib.parse import urlparse

from tests.test_chat_queue_cross_tab_http_served import (  # http_rig is a fixture
    M1,
    M2,
    _calls_for,
    _hold_turn,
    _queue_payload_rows,
    _queue_post,
    _queue_state,
    _sid,
    _wait_for,
    http_rig,
)


def _claimed(daemon, session_id: str, text: str, key: str) -> str:
    enqueued = _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": text, "idempotency_key": key})
    qid = enqueued["item"]["queue_item_id"]
    claimed = _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "tab-a"})
    assert claimed["item"]["queue_item_id"] == qid, claimed
    return qid


def _status_of(daemon, session_id: str, qid: str) -> str:
    """The row's durable status, read from the home's store (the queue door lists open rows only)."""
    return str((_queue_payload_rows(daemon.home, session_id).get(qid) or {}).get("status") or "gone")


def test_a_buffered_queued_turn_finishes_its_row_without_the_client(http_rig):
    provider, daemon = http_rig
    session_id = _sid("server-finishes-buffered")
    qid = _claimed(daemon, session_id, M2, "srv-buf-1")

    thread, box = _hold_turn(daemon, M2, session_id, turn_id="t-buf-1", queue_item_id=qid)
    thread.join(timeout=120)
    assert box["result"][0] == 200, box["result"]

    # No ``complete`` op was sent: the turn's own end finished the row.
    _wait_for(lambda: _status_of(daemon, session_id, qid) == "completed", 15, "the queued row stayed in flight")
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 15, "slot never freed")
    assert _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "tab-b"}).get("item") is None
    assert len(_calls_for(provider, M2)) == 1


def test_a_streamed_queued_turn_whose_tab_closed_mid_answer_still_finishes_its_row(http_rig):
    provider, daemon = http_rig
    session_id = _sid("server-finishes-streamed")
    qid = _claimed(daemon, session_id, M1, "srv-stream-1")  # M1 is held at the provider

    target = urlparse(daemon.base_url)
    conn = http.client.HTTPConnection(target.hostname, target.port, timeout=60)
    conn.request(
        "POST",
        "/api/chat",
        body=json.dumps(
            {
                "messages": [{"role": "user", "content": M1}],
                "stream": True,
                "session_id": session_id,
                "model": daemon.model,
                "mode": "auto",
                "turn_id": "t-stream-1",
                "on_busy": "refuse",
                "queue_item_id": qid,
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    response = conn.getresponse()
    assert response.status == 200
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the queued turn never reached the provider")
        assert _status_of(daemon, session_id, qid) == "in_flight"
    finally:
        conn.close()  # the tab closes mid-answer: its stream and its ``complete`` op are gone
    provider.release(M1)

    _wait_for(lambda: _status_of(daemon, session_id, qid) == "completed", 60, "the queued row stayed in flight")
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 15, "slot never freed")
    # Nothing is left to run it again, here or after a restart's recovery of in-flight rows.
    assert _queue_post(daemon, {"op": "claim", "session_id": session_id, "lease_owner": "tab-b"}).get("item") is None
    assert len(_calls_for(provider, M1)) == 1


def test_the_turns_end_never_overwrites_what_the_page_already_wrote(http_rig):
    provider, daemon = http_rig
    session_id = _sid("server-finishes-keeps-page-status")
    qid = _claimed(daemon, session_id, M1, "srv-keep-1")

    thread, box = _hold_turn(daemon, M1, session_id, turn_id="t-keep-1", queue_item_id=qid)
    try:
        _wait_for(lambda: len(_calls_for(provider, M1)) >= 1, 45, "the queued turn never reached the provider")
        _queue_post(daemon, {"op": "complete", "session_id": session_id, "queue_item_id": qid, "status": "cancelled"})
    finally:
        provider.release(M1)
        thread.join(timeout=120)
    assert box["result"][0] == 200, box["result"]
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 15, "slot never freed")
    assert _status_of(daemon, session_id, qid) == "cancelled"


def test_a_direct_send_with_no_claim_touches_no_queue_row(http_rig):
    provider, daemon = http_rig
    session_id = _sid("server-finishes-direct")
    pending = _queue_post(daemon, {"op": "enqueue", "session_id": session_id, "text": M1, "idempotency_key": "srv-direct-1"})
    qid = pending["item"]["queue_item_id"]

    # A send naming a row it never claimed consumes no claim, so its end finishes nothing.
    thread, box = _hold_turn(daemon, M2, session_id, turn_id="t-direct-1", queue_item_id=qid)
    thread.join(timeout=120)
    assert box["result"][0] in (200, 409), box["result"]
    assert _status_of(daemon, session_id, qid) == "pending"
    # A plain send runs and leaves the waiting row alone.
    thread, box = _hold_turn(daemon, M2, session_id, turn_id="t-direct-2")
    thread.join(timeout=120)
    assert box["result"][0] == 200, box["result"]
    assert _status_of(daemon, session_id, qid) == "pending"
