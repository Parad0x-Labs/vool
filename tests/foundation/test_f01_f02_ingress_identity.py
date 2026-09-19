"""F-01/F-02 repair proofs (independent-proof repair wave).

F-01: the streamed turn executes under the canonical A0/A2 identity
established at the accept door — request_id recorded, no turn-unknown
namespace, redelivery replays committed truth instead of re-executing.
F-02: an ACCEPTED_IDENTICAL duplicate NEVER executes — it replays the
owner's committed outcome (bounded wait) or reports typed in-progress;
the raced TOCTOU window at the accept door is closed.

Both driven through the REAL ASGI boundary (apps.vool_api_server.create_app
-> dispatch_post), mirroring the verifier's served harness.
"""
from __future__ import annotations

import functools
import json
import threading

import pytest

import storage.db as sdb

USER_TEXT = "reply with exactly: INGRESS-CANARY and nothing else"


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f01f02.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _sealed_stub(calls: list[str]):
    def _stub(*a, **k):
        calls.append("execute")
        # Emulate production run_agent: transforms inside the sealing context,
        # then A2 admission — so the streamed turn admits durably.
        from core.semantic.semantic_result_seam import (
            admit_semantic_result,
            reset_admission,
        )

        reset_admission()
        return admit_semantic_result(
            {
                "response": "INGRESS-CANARY",
                "success": True,
                "confidence": 0.9,
                "route_reason": "model_lane",
                "mode": "advice_only",
            }
        )

    return _stub


def _chat_app(monkeypatch, stub):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices, stream_agent_with_events
    from core.web.api.service import dispatch_post

    runtime = RuntimeServices(display_name="VOOL")
    app = create_app(runtime)
    app.state.post_dispatcher = functools.partial(
        dispatch_post,
        run_agent_provider=stub,
        stream_agent_with_events_provider=functools.partial(
            stream_agent_with_events, run_agent_provider=stub
        ),
    )
    return app


def _post(app, request_id, stream=False):
    body: dict = {"messages": [{"role": "user", "content": USER_TEXT}], "stream": bool(stream)}
    if stream:
        return _stream_post(app, request_id, body)
    from tests.asgi_harness import asgi_request

    return asgi_request(
        app,
        method="POST",
        path="/api/chat",
        headers={"Content-Type": "application/json", "X-Request-ID": request_id},
        body=json.dumps(body).encode(),
    )


def _stream_post(app, request_id, body: dict):
    """ASGI request that holds `receive` open through the whole streaming body
    (the shared harness sends http.disconnect after the request body, which
    makes Starlette cancel StreamingResponse mid-flight)."""
    import asyncio

    messages: list[dict] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-request-id", request_id.encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    sent_body = False
    done = threading.Event()

    async def receive():
        nonlocal sent_body
        if sent_body:
            await asyncio.get_event_loop().run_in_executor(None, done.wait)
            return {"type": "http.disconnect"}
        sent_body = True
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message):
        messages.append(message)

    async def _drive():
        try:
            await app(scope, receive, send)
        finally:
            done.set()

    asyncio.run(_drive())
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {
        k.decode("latin-1"): v.decode("latin-1") for k, v in list(start.get("headers") or [])
    }
    body_out = b"".join(
        bytes(m.get("body") or b"") for m in messages if m["type"] == "http.response.body"
    )
    return int(start["status"]), headers, body_out


def _db_counts():
    conn = sdb.get_connection()
    try:
        admissions = conn.execute(
            "SELECT sr_id, request_id FROM semantic_admissions"
        ).fetchall()
        fins = conn.execute(
            "SELECT COUNT(*) c FROM a7_finalizations"
        ).fetchone()["c"]
        reqs = conn.execute(
            "SELECT COUNT(*) c FROM invocation_requests"
        ).fetchone()["c"]
        return [dict(r) for r in admissions], int(fins), int(reqs)
    finally:
        conn.close()


def _commit_type(body_bytes: bytes) -> str:
    try:
        payload = json.loads(body_bytes.decode())
    except Exception:
        return ""
    return str(((payload.get("vool_response_commit") or {}).get("type")) or "")


# --- F-01: streaming identity -------------------------------------------------


def test_streamed_turn_carries_ingress_identity_and_redelivery_replays(
    fresh_store, monkeypatch
):
    monkeypatch.setenv("VOOL_ACCEPT_ONCE_REPLAY_WAIT", "5")
    calls: list[str] = []
    app = _chat_app(monkeypatch, _sealed_stub(calls))

    status1, _, body1 = _post(app, "req-f01-stream", stream=True)
    assert status1 == 200, body1[:400]

    admissions, fins, _reqs = _db_counts()
    assert len(admissions) == 1, admissions
    # The streamed turn must carry the canonical request binding — no more
    # request_id='' / sr:turn-unknown:* orphan rows.
    assert admissions[0]["request_id"].startswith("req:"), admissions
    assert "turn-unknown" not in str(admissions[0]["sr_id"]), admissions
    assert fins == 1

    # Sequential redelivery of the SAME streamed request: pure read of
    # committed truth — no second execution, no second admission/commit.
    status2, _, body2 = _post(app, "req-f01-stream", stream=True)
    assert status2 == 200, body2[:400]
    assert _commit_type(body2) == "response.replay", body2[:400]

    admissions2, fins2, _r2 = _db_counts()
    assert len(admissions2) == 1, admissions2
    assert fins2 == 1, fins2
    assert len(calls) == 1, calls


# --- F-02: raced identical redelivery — one owner, zero double-execution ------


def test_raced_same_request_id_executes_once_never_two_commits(
    fresh_store, monkeypatch
):
    monkeypatch.setenv("VOOL_ACCEPT_ONCE_REPLAY_WAIT", "15")
    calls: list[str] = []
    app = _chat_app(monkeypatch, _sealed_stub(calls))

    barrier = threading.Barrier(2)
    results: list = []

    def _twin() -> None:
        barrier.wait()
        results.append(_post(app, "req-f02-race"))

    t1, t2 = threading.Thread(target=_twin), threading.Thread(target=_twin)
    t1.start(); t2.start(); t1.join(60); t2.join(60)

    statuses = sorted(r[0] for r in results)
    types = sorted(_commit_type(r[2]) for r in results)

    assert statuses == [200, 200], statuses
    # Exactly ONE fresh commit; the loser replays or reports typed in-progress.
    assert types.count("response.commit") == 1, types
    assert all(t in {"response.commit", "response.replay", "response.in_progress"} for t in types), types
    admissions, fins, reqs = _db_counts()
    assert reqs == 1, reqs
    assert len(admissions) == 1, admissions
    assert fins == 1, fins
    assert len(calls) == 1, f"turn executed {len(calls)} times"


def test_duplicate_before_owner_finalizes_reports_in_progress_not_execution(
    fresh_store, monkeypatch
):
    """A duplicate arriving while the owner is still executing must never fall
    through into a second dispatch (the old TOCTOU fall-through)."""
    monkeypatch.setenv("VOOL_ACCEPT_ONCE_REPLAY_WAIT", "5")
    release = threading.Event()
    calls: list[str] = []

    def _slow_stub(*a, **k):
        calls.append("execute")
        release.wait(20)
        from core.semantic.semantic_result_seam import (
            admit_semantic_result,
            reset_admission,
        )

        reset_admission()
        return admit_semantic_result(
            {
                "response": "INGRESS-CANARY",
                "success": True,
                "confidence": 0.9,
                "route_reason": "model_lane",
                "mode": "advice_only",
            }
        )

    app = _chat_app(monkeypatch, _slow_stub)

    winner_outcome: list = []

    def _winner() -> None:
        winner_outcome.append(_post(app, "req-f02-inflight"))

    wt = threading.Thread(target=_winner)
    wt.start()
    # Wait until the owner is provably mid-turn (stub entered).
    deadline_check = threading.Event()

    import time as _time

    _deadline = _time.monotonic() + 10
    while not calls and _time.monotonic() < _deadline:
        _time.sleep(0.01)
    assert calls, "owner never started executing"

    status, _, body = _post(app, "req-f02-inflight")
    # Loser observes typed in-progress (owner has not finalized yet).
    assert status == 200
    assert _commit_type(body) == "response.in_progress", body[:400]

    release.set()
    wt.join(30)
    assert wt.is_alive() is False

    admissions, fins, _reqs = _db_counts()
    assert len(admissions) == 1, admissions
    assert fins == 1, fins
    assert len(calls) == 1, f"turn executed {len(calls)} times"
