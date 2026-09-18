"""Chat-mutating endpoints are owner-local (entry-surface audit, 2026-08-29).

/api/chat/cancel, /api/chat/pin and /api/chat/queue are state-changing
endpoints whose own comments claimed "owner-local, loopback" — but only
Host/Origin header guards stood in the way, and headers are caller-supplied.
Under the documented non-loopback threat model (core/request_trust.py) a
remote peer could cancel turns, pin into any session, and enqueue/claim/
complete queue items. The gate must be the same TCP-peer-derived check every
other owner-local surface uses: a remote client_host gets 403
owner_local_required; the loopback owner is untouched.
"""

from __future__ import annotations

import json

import pytest

MUTATING_CHAT_PATHS = (
    "/api/chat/cancel",
    "/api/chat/pin",
    "/api/chat/queue",
)


def _post(path: str, body: dict, host: str):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path=path,
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def _get_queue(host: str):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/api/chat/queue",
        query={"session": ["sess-owner-local"]},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


@pytest.mark.parametrize("path", MUTATING_CHAT_PATHS)
def test_a_remote_peer_cannot_cancel_pin_or_enqueue(path: str) -> None:
    body = {"session_id": "sess-x", "turn_id": "t1", "message": "hi", "op": "enqueue"}
    status, payload = _post(path, body, "203.0.113.9")
    assert status == 403
    assert payload.get("error") == "owner_local_required"


def test_a_remote_peer_cannot_read_the_queue() -> None:
    status, payload = _get_queue("203.0.113.9")
    assert status == 403
    assert payload.get("error") == "owner_local_required"


def test_loopback_owner_still_reaches_the_queue() -> None:
    # The gate refuses the remote peer, not the feature: the owner's read
    # still returns the queue shape.
    status, payload = _get_queue("127.0.0.1")
    assert status == 200
    assert payload.get("ok") is True or "queue" in payload


def test_loopback_cancel_reaches_its_handler_not_the_gate() -> None:
    # Loopback + well-formed body must get PAST the owner-local gate: a
    # not_found state proves the request reached the cancel logic itself.
    status, payload = _post(
        "/api/chat/cancel",
        {"session_id": "sess-owner-local", "turn_id": "no-such-turn"},
        "127.0.0.1",
    )
    assert status == 200
    assert payload.get("ok") is True
    assert payload.get("state") in {"not_found", "cancelled"}


def test_the_gate_precedes_every_other_check_on_the_mutation_paths() -> None:
    # Even a malformed body gets the 403 on a remote host: the gate is the
    # FIRST check, so header tricks cannot buy reachability.
    for path in MUTATING_CHAT_PATHS:
        status, payload = _post(path, {"bogus_field": "x"}, "203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required"
