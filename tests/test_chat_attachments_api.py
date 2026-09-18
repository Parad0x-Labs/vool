"""The attachment door on the served API, and the chat turn that spends what it staged.

Driven through the REAL starlette application (`apps.vool_api_server.create_app`) with the ASGI
harness, so the raw-body upload branch, the loopback/Origin guards and the JSON error shapes are
the ones a browser meets -- not a helper called directly. The chat turn is driven through
`dispatch_post` with a fake agent that records the `source_context` it was handed, which is the
only way to prove the runtime received the attachment as bounded evidence and never as a path.
"""

from __future__ import annotations

import functools
import json
import urllib.parse
from typing import Any

import pytest

from core import runtime_paths
from tests.asgi_harness import asgi_request
from tests.test_chat_attachments_authority import tiny_jpeg, tiny_png

SESSION = "openclaw:cccccccccccccccccccc"
OTHER = "openclaw:dddddddddddddddddddd"
UPLOAD = "/api/chat/attachments/upload"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _app():
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _upload(app, *, name: str, media_type: str, data: bytes, session: str = SESSION, origin: str | None = None, headers: dict[str, str] | None = None):
    hdrs = {
        "Content-Type": "application/octet-stream",
        "X-Vool-Attachment-Name": urllib.parse.quote(name, safe=""),
        "X-Vool-Attachment-Type": media_type,
        "X-Vool-Session-Id": session,
        "Host": "127.0.0.1",
    }
    if origin:
        hdrs["Origin"] = origin
    hdrs.update(headers or {})
    status, _, body = asgi_request(app, method="POST", path=UPLOAD, headers=hdrs, body=data)
    return status, json.loads(body or b"{}")


def _json_post(app, path: str, payload: dict[str, Any]):
    status, _, body = asgi_request(
        app, method="POST", path=path, headers={"Content-Type": "application/json", "Host": "127.0.0.1"}, body=json.dumps(payload).encode("utf-8")
    )
    return status, json.loads(body or b"{}")


def _get(app, path: str):
    status, headers, body = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
    return status, headers, body


def test_limits_are_published_for_the_composer() -> None:
    status, _, body = _get(_app(), "/api/chat/attachments/limits")
    assert status == 200
    limits = json.loads(body)
    assert limits["max_files_per_turn"] >= 4 and limits["max_bytes_per_file"] >= 4 * 1024 * 1024
    assert limits["accept"] and ".txt" in limits["accept"] and "image/png" in limits["accept"]


def test_a_raw_upload_stages_a_text_file_and_the_response_names_no_path() -> None:
    app = _app()
    status, payload = _upload(app, name="notes é.txt", media_type="text/plain", data=b"hello over http\n")
    assert status == 201, payload
    att = payload["attachment"]
    assert att["id"].startswith("att_") and att["kind"] == "text" and att["name"] == "notes é.txt"
    assert att["size_bytes"] == 16 and att["state"] == "staged"
    assert "path" not in att and str(runtime_paths.active_vool_home()) not in json.dumps(payload)
    status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote(SESSION))
    assert status == 200
    listed = json.loads(body)["attachments"]
    assert [a["id"] for a in listed] == [att["id"]]
    # Another chat sees nothing of it.
    status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote(OTHER))
    assert json.loads(body)["attachments"] == []


def test_an_image_upload_stages_and_previews_only_for_its_owner() -> None:
    app = _app()
    status, payload = _upload(app, name="shot.png", media_type="image/png", data=tiny_png(with_private_metadata=True))
    assert status == 201, payload
    att = payload["attachment"]
    assert att["kind"] == "image" and att["media_type"] == "image/png"
    status, headers, body = _get(app, f"/api/chat/attachments/preview?session={urllib.parse.quote(SESSION)}&id={att['id']}")
    assert status == 200 and headers.get("content-type", "").startswith("image/png")
    assert body.startswith(b"\x89PNG") and b"GPSLatitude" not in body
    status, _, _ = _get(app, f"/api/chat/attachments/preview?session={urllib.parse.quote(OTHER)}&id={att['id']}")
    assert status == 404


def test_refusals_carry_a_code_a_human_message_and_the_right_status() -> None:
    app = _app()
    from core import chat_attachments

    status, payload = _upload(app, name="huge.txt", media_type="text/plain", data=b"x" * (chat_attachments.MAX_BYTES_PER_FILE + 1))
    assert status == 413 and payload["ok"] is False and payload["error"] == "too_large" and payload["message"]
    status, payload = _upload(app, name="tool.exe", media_type="application/octet-stream", data=b"MZ\x90")
    assert status == 415 and payload["error"] == "unsupported_type"
    status, payload = _upload(app, name="photo.png", media_type="image/png", data=tiny_jpeg())
    assert status == 422 and payload["error"] == "content_mismatch"
    status, payload = _upload(app, name="../../etc/passwd", media_type="text/plain", data=b"root:x")
    assert status == 422 and payload["error"] == "name_rejected"
    status, payload = _upload(app, name="ok.txt", media_type="text/plain", data=b"x", session="")
    assert status == 400 and payload["error"] == "missing_session"
    # The transport's global JSON ceiling does not apply to this door, but a body over the
    # attachment limit is still refused before it is held whole in memory for validation.
    status, payload = _upload(app, name="ok.txt", media_type="text/plain", data=b"x" * (chat_attachments.MAX_BYTES_PER_FILE + 2))
    assert status == 413
    # Nothing was staged by any refusal.
    status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote(SESSION))
    assert json.loads(body)["attachments"] == []


def test_cross_origin_and_remote_peers_cannot_reach_the_door() -> None:
    app = _app()
    status, _payload = _upload(app, name="ok.txt", media_type="text/plain", data=b"x", origin="https://evil.example")
    assert status == 403
    from tests.asgi_harness import asgi_request as _req

    scope_headers = {
        "Content-Type": "application/octet-stream",
        "X-Vool-Attachment-Name": "ok.txt",
        "X-Vool-Attachment-Type": "text/plain",
        "X-Vool-Session-Id": SESSION,
        "Host": "127.0.0.1",
    }
    # A non-loopback peer: the service refuses owner-only doors on the TCP peer, never on a header.
    from core.web.api import service

    resp = service.dispatch_upload(
        path=UPLOAD,
        raw_body=b"x",
        headers=scope_headers,
        runtime=None,
        client_host="203.0.113.9",
    )
    assert resp.status == 403
    del _req


def test_remove_deletes_only_the_owners_staged_item() -> None:
    app = _app()
    _, payload = _upload(app, name="a.txt", media_type="text/plain", data=b"a")
    att = payload["attachment"]
    status, out = _json_post(app, "/api/chat/attachments/remove", {"session_id": OTHER, "attachment_id": att["id"]})
    assert status == 200 and out["removed"] is False
    status, out = _json_post(app, "/api/chat/attachments/remove", {"session_id": SESSION, "attachment_id": att["id"]})
    assert status == 200 and out["removed"] is True
    status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote(SESSION))
    assert json.loads(body)["attachments"] == []
    status, out = _json_post(app, "/api/chat/attachments/remove", {"session_id": SESSION, "attachment_id": "../x"})
    assert status == 400


def _chat(app, *, body: dict[str, Any], captured: list[dict[str, Any]], session: str = SESSION):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    def fake_agent(runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
        captured.append({"text": text, "session_id": session_id, "source_context": dict(source_context or {})})
        # Production persists the turn from inside the agent (core/agent_runtime/chat_surface.py);
        # the fake does the same so the transcript reader is exercised for real.
        from core.persistent_memory import append_conversation_event

        append_conversation_event(session_id=str(session_id), user_input=text, assistant_output="seen", source_context=source_context)
        return {"response": "seen", "confidence": 0.9}

    return dispatch_post(
        path="/api/chat",
        body={"session_id": session, "stream": False, **body},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
        run_agent_provider=fake_agent,
    )


def test_a_chat_turn_binds_its_staged_attachments_and_hands_them_over_as_evidence() -> None:
    app = _app()
    _, text_payload = _upload(app, name="notes.txt", media_type="text/plain", data=b"the answer is 42\n")
    _, image_payload = _upload(app, name="shot.png", media_type="image/png", data=tiny_png())
    ids = [text_payload["attachment"]["id"], image_payload["attachment"]["id"]]
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "what do these say?"}], "turn_id": "turn-x", "attachments": ids}, captured=captured)
    assert resp.status == 200, resp.body
    assert len(captured) == 1
    context = captured[0]["source_context"]
    evidence = context["external_evidence"]
    assert [item["attachment_id"] for item in evidence] == ids
    assert evidence[0]["kind"] == "text" and evidence[0]["text"] == "the answer is 42\n"
    assert evidence[1]["kind"] == "image"
    assert all(item["origin"] == "chat_attachment" for item in evidence)
    assert context["attachment_turn_id"] == "turn-x"
    assert str(runtime_paths.active_vool_home()) not in json.dumps(context, default=str)
    # The turn is over: the bytes are gone, the receipt survives, and the transcript names them.
    from core import chat_attachments

    assert not list(chat_attachments.stage_dir().glob("*.bin"))
    receipt = chat_attachments.receipt_for_turn(session_id=SESSION, turn_id="turn-x")
    assert [r["name"] for r in receipt] == ["notes.txt", "shot.png"]
    _status, _, body = _get(app, "/api/chat/history?session=" + urllib.parse.quote(SESSION))
    history = json.loads(body)["messages"]
    user_rows = [m for m in history if m["role"] == "user"]
    assert user_rows and [a["name"] for a in user_rows[-1]["attachments"]] == ["notes.txt", "shot.png"]
    assert all(set(a) <= {"id", "name", "kind", "media_type", "size_bytes", "outcome"} for a in user_rows[-1]["attachments"])


def test_a_text_only_turn_is_byte_for_byte_unchanged() -> None:
    app = _app()
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "just text"}], "turn_id": "turn-t"}, captured=captured)
    assert resp.status == 200
    context = captured[0]["source_context"]
    assert "external_evidence" not in context and "attachment_turn_id" not in context
    _status, _, body = _get(app, "/api/chat/history?session=" + urllib.parse.quote(SESSION))
    user_rows = [m for m in json.loads(body)["messages"] if m["role"] == "user"]
    assert user_rows and "attachments" not in user_rows[-1]


def test_another_chats_attachment_forged_ids_and_client_paths_are_refused_before_any_work() -> None:
    app = _app()
    _, payload = _upload(app, name="mine.txt", media_type="text/plain", data=b"mine", session=OTHER)
    foreign_id = payload["attachment"]["id"]
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "read it"}], "turn_id": "turn-1", "attachments": [foreign_id]}, captured=captured)
    assert resp.status == 422
    assert json.loads(resp.body)["error"] == "attachment_rejected"
    assert captured == []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "read it"}], "turn_id": "turn-1", "attachments": ["att_" + "f" * 32]}, captured=captured)
    assert resp.status == 422 and captured == []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "read it"}], "turn_id": "turn-1", "attachments": [{"path": "/etc/passwd"}]}, captured=captured)
    assert resp.status == 422 and captured == []
    # A client-supplied evidence item that pretends to be a staged attachment is stripped, not honoured.
    resp = _chat(
        app,
        body={
            "messages": [{"role": "user", "content": "read it"}],
            "turn_id": "turn-2",
            "source_context": {"external_evidence": [{"kind": "text", "attachment_id": foreign_id, "origin": "chat_attachment", "path": "/etc/passwd"}]},
        },
        captured=captured,
    )
    assert resp.status == 200
    assert not any(item.get("attachment_id") for item in captured[-1]["source_context"].get("external_evidence") or [])
    # The foreign item is still staged for its real owner: a refusal spends nothing.
    from core import chat_attachments

    assert chat_attachments.get_record(foreign_id)["state"] == "staged"


def test_an_attachment_spent_by_one_turn_cannot_ride_a_later_turn_or_a_retry_of_another() -> None:
    app = _app()
    _, payload = _upload(app, name="once.txt", media_type="text/plain", data=b"once")
    att_id = payload["attachment"]["id"]
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "first"}], "turn_id": "turn-1", "attachments": [att_id]}, captured=captured)
    assert resp.status == 200 and len(captured) == 1
    resp = _chat(app, body={"messages": [{"role": "user", "content": "second"}], "turn_id": "turn-2", "attachments": [att_id]}, captured=captured)
    assert resp.status == 422 and len(captured) == 1


def test_the_queue_only_accepts_the_sessions_own_staged_ids() -> None:
    app = _app()
    _, payload = _upload(app, name="q.txt", media_type="text/plain", data=b"q")
    att_id = payload["attachment"]["id"]
    status, out = _json_post(app, "/api/chat/queue", {"op": "enqueue", "session_id": SESSION, "text": "later", "attachments": [att_id]})
    assert status == 200 and out["item"]["payload"]["attachments"] == [att_id]
    status, out = _json_post(app, "/api/chat/queue", {"op": "enqueue", "session_id": OTHER, "text": "steal", "attachments": [att_id]})
    assert status == 422
    status, out = _json_post(app, "/api/chat/queue", {"op": "enqueue", "session_id": SESSION, "text": "junk", "attachments": [{"path": "/etc/passwd"}]})
    assert status == 422


def test_activity_records_what_was_attached_refused_and_read_without_contents() -> None:
    app = _app()
    secret_text = b"password=hunter2-super-secret\n"
    _, payload = _upload(app, name="creds.txt", media_type="text/plain", data=secret_text)
    att_id = payload["attachment"]["id"]
    _upload(app, name="photo.png", media_type="image/png", data=tiny_jpeg())  # refused: forged
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "what is in it?"}], "turn_id": "turn-1", "attachments": [att_id]}, captured=captured)
    assert resp.status == 200
    from core.runtime_task_events import list_runtime_session_events

    events = list_runtime_session_events(SESSION, limit=200)
    kinds = [e["event_type"] for e in events]
    assert "attachment_staged" in kinds and "attachment_refused" in kinds and "attachment_bound" in kinds and "attachment_released" in kinds
    dumped = json.dumps(events, default=str)
    assert "hunter2" not in dumped
    assert str(runtime_paths.active_vool_home()) not in dumped
    # The ledger flattens `details` onto the row (the Activity panel reads it that way).
    refused = next(e for e in events if e["event_type"] == "attachment_refused")
    assert refused["code"] == "content_mismatch" and "photo.png" in refused["message"]
    staged = next(e for e in events if e["event_type"] == "attachment_staged")
    assert staged["attachment_id"] == att_id and staged["size_bytes"] == len(secret_text)
    bound = next(e for e in events if e["event_type"] == "attachment_bound")
    assert bound["client_turn_id"] == "turn-1" and bound["attachment_id"] == att_id
    released = next(e for e in events if e["event_type"] == "attachment_released")
    assert released["attachment_id"] == att_id and released["outcome"] == "ignored"  # the fake agent read nothing


def test_the_legacy_http_handler_and_starlette_share_one_upload_door() -> None:
    """The desktop bundle serves through starlette; the legacy BaseHTTPRequestHandler is still a
    door. Both must reach the same authority -- a raw body must not be json-decoded to death."""
    from core.web.api import service

    assert callable(getattr(service, "dispatch_upload", None))
    assert callable(getattr(service, "is_raw_upload_path", None))
    assert service.is_raw_upload_path(UPLOAD) is True
    assert service.is_raw_upload_path("/api/chat") is False
    app = _app()
    status, _, body = asgi_request(app, method="POST", path=UPLOAD, headers={"Content-Type": "application/octet-stream", "Host": "127.0.0.1"}, body=b"")
    assert status == 400 and json.loads(body)["error"] in {"empty body", "empty_file", "missing_session"}
    partial = functools.partial
    del partial


# --- One chat identity across every attachment door -------------------------------------------
#
# Measured 2026-09-09 on the served build: uploading with a plain handle returned HTTP 200 and a
# record stamped with that handle, and the very next /api/chat carrying its id was refused
# `{"error":"attachment_rejected","code":"not_owned"}` (422). The doors disagreed: /api/chat
# folded a non-canonical handle into `openclaw:<digest>` while every attachment door stored the
# raw string. An attachment could be staged and then never sent, and nothing in the refusal said
# why. Every test above uses a canonical SESSION -- the one shape the desktop page mints -- which
# is exactly why the split was invisible.

PLAIN = "acceptance-v2-20260908"          # what an API client, a script or a driver actually sends
PLAIN_OTHER = "acceptance-v2-20260908-b"  # a different chat, one character apart


def test_a_plain_chat_handle_can_stage_an_attachment_and_then_send_it() -> None:
    app = _app()
    _, payload = _upload(app, name="notes.txt", media_type="text/plain", data=b"line one\nthe orchard gate stays open\n", session=PLAIN)
    att_id = payload["attachment"]["id"]
    # The staged chip is visible to the same handle that staged it.
    _status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote(PLAIN))
    assert [a["id"] for a in json.loads(body)["attachments"]] == [att_id]

    captured: list[dict[str, Any]] = []
    resp = _chat(
        app,
        body={"messages": [{"role": "user", "content": "what is the second line?"}], "turn_id": "turn-plain", "attachments": [att_id]},
        captured=captured,
        session=PLAIN,
    )
    assert resp.status == 200, resp.body  # was 422 attachment_rejected/not_owned
    evidence = captured[0]["source_context"]["external_evidence"]
    assert [item["attachment_id"] for item in evidence] == [att_id]
    assert evidence[0]["text"] == "line one\nthe orchard gate stays open\n"


def test_a_plain_handle_and_its_resolved_form_name_the_same_chat() -> None:
    """The property the doors rely on: resolving is idempotent, so a door that has already
    resolved a handle and one that has not both land on the same chat."""
    from core.chat_session_identity import canonical_chat_session_id

    app = _app()
    resolved = canonical_chat_session_id(PLAIN)
    assert resolved != PLAIN and canonical_chat_session_id(resolved) == resolved
    _, payload = _upload(app, name="staged-plain.txt", media_type="text/plain", data=b"x\n", session=PLAIN)
    att_id = payload["attachment"]["id"]
    # Staged under the plain handle, listed under the resolved one: one chat, not two.
    _status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote(resolved))
    assert [a["id"] for a in json.loads(body)["attachments"]] == [att_id]
    # And a turn addressed by the resolved handle spends what the plain handle staged.
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "read it"}], "turn_id": "turn-r", "attachments": [att_id]}, captured=captured, session=resolved)
    assert resp.status == 200, resp.body
    assert [i["attachment_id"] for i in captured[0]["source_context"]["external_evidence"]] == [att_id]


def test_resolving_the_handle_does_not_open_one_chats_attachments_to_another() -> None:
    """The ownership law is unchanged: near-identical plain handles stay separate chats, and a
    handle that only LOOKS canonical is resolved rather than honoured as an identity."""
    app = _app()
    _, payload = _upload(app, name="private.txt", media_type="text/plain", data=b"secret\n", session=PLAIN)
    att_id = payload["attachment"]["id"]

    _status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote(PLAIN_OTHER))
    assert json.loads(body)["attachments"] == []
    status, _, _ = _get(app, f"/api/chat/attachments/preview?session={urllib.parse.quote(PLAIN_OTHER)}&id={att_id}")
    assert status == 404
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "read it"}], "turn_id": "turn-thief", "attachments": [att_id]}, captured=captured, session=PLAIN_OTHER)
    assert resp.status == 422 and json.loads(resp.body)["code"] == "not_owned"
    # A malformed canonical-looking handle is not an identity: it is resolved like any other
    # string, so it cannot address the chat whose digest it imitates.
    status, _, body = _get(app, "/api/chat/attachments?session=" + urllib.parse.quote("openclaw:zzzzzzzzzzzzzzzzzzzz"))
    assert json.loads(body)["attachments"] == []
