"""The served contract for pasted documents: the same door, the same session ownership, retained.

Desktop and mobile share this contract because it is the API, not the page: the composer (or any
client) posts the exact bytes to the ONE raw upload door with `X-Vool-Attachment-Source: paste`,
gets a document id back, and sends the turn with that id. What is pinned here through the real
starlette app:

- the door stages a document by source header, names it, and publishes the threshold in limits;
- the preview door serves a text document's exact bytes to its owner, before and after the turn,
  and answers 410 once the document is erased -- never another chat's bytes;
- the documents listing is per chat;
- a turn binds the document, hands the runtime the exact text as evidence, keeps the bytes after
  the turn, and the transcript row names the document with its id, sha256 and outcome;
- a retried send of the same turn is the same turn;
- the remove door erases a sent document for its owner only, and Activity records it;
- deleting the chat erases its documents and nothing of another chat's;
- oversized, binary and non-UTF-8 pastes are refused at the door with typed codes.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any

import pytest

from core import chat_attachments as ca
from core import runtime_paths
from tests.test_chat_attachments_api import _app, _get, _json_post, _upload

# Canonical ids (``openclaw:`` + 20 hex digits): the door passes those through verbatim, so the
# chat that pasted is the chat that sends. Anything else would be re-hashed into a fresh session.
SESSION = "openclaw:d0c0d0c0d0c0d0c0aaaa"
OTHER = "openclaw:d0c0d0c0d0c0d0c0bbbb"
PASTE = ("2026-09-02T10:00:00Z INFO service ready ✅ 中文\ttab\r\n" * 120) + "tail without newline"
PASTE_BYTES = PASTE.encode("utf-8")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _paste(app, data: bytes = PASTE_BYTES, *, session: str = SESSION, name: str = "", headers: dict[str, str] | None = None):
    hdrs = {"X-Vool-Attachment-Source": "paste"}
    hdrs.update(headers or {})
    return _upload(app, name=name, media_type="text/plain", data=data, session=session, headers=hdrs)


def _chat(app, *, body: dict[str, Any], captured: list[dict[str, Any]], session: str = SESSION):
    from tests.test_chat_attachments_api import _chat as base_chat

    return base_chat(app, body=body, captured=captured, session=session)


def test_the_door_stages_a_document_by_source_header_and_limits_publish_the_threshold() -> None:
    app = _app()
    status, _headers, body = _get(app, "/api/chat/attachments/limits")
    assert status == 200
    limits = json.loads(body)
    assert limits["document_threshold_chars"] == ca.DOCUMENT_THRESHOLD_CHARS == 4000
    status, payload = _paste(app)
    assert status == 201, payload
    record = payload["attachment"]
    # Timestamped lines are a log to the one type authority; the name says so.
    assert record["document"] is True and record["name"] == "pasted-1.log" and record["media_type"] == "text/plain"
    assert record["size_bytes"] == len(PASTE_BYTES) and record["sha256"] == hashlib.sha256(PASTE_BYTES).hexdigest()
    assert record["chars"] == len(PASTE) and record["lines"] == PASTE.count("\n") + 1
    assert str(runtime_paths.active_vool_home()) not in json.dumps(payload)
    # Without the source header the same bytes are a picked file and need a name (unchanged contract).
    status, payload = _upload(app, name="", media_type="text/plain", data=PASTE_BYTES)
    assert status == 422 and payload["error"] == "name_rejected"


def test_the_preview_door_serves_exact_bytes_to_the_owner_before_and_after_the_turn_then_410_once_erased() -> None:
    app = _app()
    _, payload = _paste(app)
    doc_id = payload["attachment"]["id"]
    path = f"/api/chat/attachments/preview?session={urllib.parse.quote(SESSION)}&id={doc_id}"
    status, headers, body = _get(app, path)
    assert status == 200 and body == PASTE_BYTES
    assert headers.get("content-type", "").startswith("text/plain") and "utf-8" in headers.get("content-type", "").lower()
    assert headers.get("x-content-type-options") == "nosniff"
    status, _, _ = _get(app, f"/api/chat/attachments/preview?session={urllib.parse.quote(OTHER)}&id={doc_id}")
    assert status == 404, "another chat previewed a document"
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "what does this say?"}], "turn_id": "turn-d", "attachments": [doc_id]}, captured=captured)
    assert resp.status == 200, resp.body
    status, _, body = _get(app, path)
    assert status == 200 and body == PASTE_BYTES, "the bytes did not survive the turn"
    status, payload = _json_post(app, "/api/chat/attachments/remove", {"session_id": SESSION, "attachment_id": doc_id})
    assert status == 200 and payload == {"ok": True, "removed": True}
    status, _, _ = _get(app, path)
    assert status == 410


def test_documents_are_listed_per_chat() -> None:
    app = _app()
    _, mine = _paste(app)
    _, theirs = _paste(app, b"other chat body\n" * 300, session=OTHER)
    captured: list[dict[str, Any]] = []
    _chat(app, body={"messages": [{"role": "user", "content": "keep"}], "turn_id": "turn-l", "attachments": [mine["attachment"]["id"]]}, captured=captured)
    status, _, body = _get(app, f"/api/chat/attachments/documents?session={urllib.parse.quote(SESSION)}")
    assert status == 200
    listed = json.loads(body)["documents"]
    assert [d["id"] for d in listed] == [mine["attachment"]["id"]]
    assert listed[0]["turn_id"] == "turn-l" and listed[0]["state"] == "released" and listed[0]["document"] is True
    # A paste nobody sent yet is a draft, not a document of the chat; once sent, it is listed there.
    status, _, body = _get(app, f"/api/chat/attachments/documents?session={urllib.parse.quote(OTHER)}")
    assert json.loads(body)["documents"] == []
    _chat(app, body={"messages": [{"role": "user", "content": "keep"}], "turn_id": "turn-o", "attachments": [theirs["attachment"]["id"]]}, captured=captured, session=OTHER)
    status, _, body = _get(app, f"/api/chat/attachments/documents?session={urllib.parse.quote(OTHER)}")
    assert [d["id"] for d in json.loads(body)["documents"]] == [theirs["attachment"]["id"]]
    status, _, body = _get(app, f"/api/chat/attachments/documents?session={urllib.parse.quote(SESSION)}")
    assert [d["id"] for d in json.loads(body)["documents"]] == [mine["attachment"]["id"]], "another chat's document crossed over"


def test_a_turn_binds_the_document_hands_over_exact_text_keeps_the_bytes_and_the_transcript_names_it() -> None:
    app = _app()
    _, payload = _paste(app)
    doc_id = payload["attachment"]["id"]
    captured: list[dict[str, Any]] = []
    resp = _chat(app, body={"messages": [{"role": "user", "content": "summarise"}], "turn_id": "turn-1", "attachments": [doc_id]}, captured=captured)
    assert resp.status == 200, resp.body
    evidence = captured[0]["source_context"]["external_evidence"]
    assert len(evidence) == 1 and evidence[0]["text"] == PASTE and evidence[0]["document"] is True
    assert evidence[0]["origin"] == "chat_attachment" and evidence[0]["attachment_id"] == doc_id
    assert (ca.stage_dir() / f"{doc_id}.bin").read_bytes() == PASTE_BYTES
    _, _, body = _get(app, "/api/chat/history?session=" + urllib.parse.quote(SESSION))
    user_rows = [m for m in json.loads(body)["messages"] if m["role"] == "user"]
    receipt = user_rows[-1]["attachments"]
    assert len(receipt) == 1
    assert receipt[0]["id"] == doc_id and receipt[0]["document"] is True
    assert receipt[0]["sha256"] == hashlib.sha256(PASTE_BYTES).hexdigest()
    assert receipt[0]["chars"] == len(PASTE) and receipt[0]["size_bytes"] == len(PASTE_BYTES)
    assert receipt[0]["outcome"] == "ignored", "the fake agent read nothing: the receipt must say so"
    # The same turn sent again (a retry after a failed answer) is the same turn: bound again, no refusal.
    resp = _chat(app, body={"messages": [{"role": "user", "content": "summarise"}], "turn_id": "turn-1", "attachments": [doc_id]}, captured=captured)
    assert resp.status == 200, resp.body
    assert captured[1]["source_context"]["external_evidence"][0]["text"] == PASTE
    # A different turn cannot claim it; the identity belongs to turn-1 and later turns get it carried.
    resp = _chat(app, body={"messages": [{"role": "user", "content": "again"}], "turn_id": "turn-2", "attachments": [doc_id]}, captured=captured)
    assert resp.status == 422 and json.loads(resp.body)["code"] == "already_bound"


def test_remove_erases_a_sent_document_for_its_owner_only_and_activity_records_it() -> None:
    app = _app()
    _, payload = _paste(app)
    doc_id = payload["attachment"]["id"]
    captured: list[dict[str, Any]] = []
    _chat(app, body={"messages": [{"role": "user", "content": "keep"}], "turn_id": "turn-1", "attachments": [doc_id]}, captured=captured)
    status, payload = _json_post(app, "/api/chat/attachments/remove", {"session_id": OTHER, "attachment_id": doc_id})
    assert status == 200 and payload["removed"] is False
    assert (ca.stage_dir() / f"{doc_id}.bin").exists()
    status, payload = _json_post(app, "/api/chat/attachments/remove", {"session_id": SESSION, "attachment_id": doc_id})
    assert status == 200 and payload["removed"] is True
    assert not (ca.stage_dir() / f"{doc_id}.bin").exists()
    record = ca.get_record(doc_id)
    assert record is not None and record["state"] == "erased"
    _, _, body = _get(app, f"/api/runtime/events?session={urllib.parse.quote(SESSION)}&limit=200")
    events = json.loads(body)["events"]
    kinds = [e.get("event_type") for e in events]
    assert "attachment_staged" in kinds and "attachment_bound" in kinds and "attachment_erased" in kinds, kinds
    assert "service ready" not in json.dumps(events), "a document's contents leaked into Activity"
    # The transcript still names the document by identity.
    _, _, body = _get(app, "/api/chat/history?session=" + urllib.parse.quote(SESSION))
    user_rows = [m for m in json.loads(body)["messages"] if m["role"] == "user"]
    assert user_rows[-1]["attachments"][0]["id"] == doc_id


def test_deleting_the_chat_erases_its_documents_and_nothing_of_another_chat() -> None:
    app = _app()
    _, mine = _paste(app)
    _, draft = _paste(app, b"never sent draft\n" * 300)
    _, theirs = _paste(app, b"other chat body\n" * 300, session=OTHER)
    captured: list[dict[str, Any]] = []
    _chat(app, body={"messages": [{"role": "user", "content": "keep"}], "turn_id": "turn-1", "attachments": [mine["attachment"]["id"]]}, captured=captured)
    _chat(app, body={"messages": [{"role": "user", "content": "keep"}], "turn_id": "turn-1", "attachments": [theirs["attachment"]["id"]]}, captured=captured, session=OTHER)
    status, payload = _json_post(app, "/api/chat/session", {"session_id": SESSION, "delete": True})
    assert status == 200 and payload["deleted"] is True, payload
    assert ca.get_record(mine["attachment"]["id"]) is None and ca.get_record(draft["attachment"]["id"]) is None
    assert not (ca.stage_dir() / f"{mine['attachment']['id']}.bin").exists()
    assert (ca.stage_dir() / f"{theirs['attachment']['id']}.bin").exists()
    assert ca.get_record(theirs["attachment"]["id"]) is not None


@pytest.mark.parametrize(
    "data,code,status",
    [
        (b"y" * (ca.MAX_BYTES_PER_FILE + 1), "too_large", 413),
        (b"binary \x00 looking " * 300, "not_text", 422),
        (b"\xff\xfe\xfd not utf-8 " * 300, "not_text", 422),
    ],
)
def test_oversized_binary_and_non_utf8_pastes_fail_visibly_at_the_door(data: bytes, code: str, status: int) -> None:
    app = _app()
    got_status, payload = _paste(app, data)
    assert got_status == status, payload
    assert payload["ok"] is False and payload["error"] == code and payload["message"]
    assert not list(ca.stage_dir().glob("*.bin")) if ca.stage_dir().exists() else True


def test_a_document_only_turn_is_answered_truthfully_it_is_kept_not_dropped() -> None:
    """The front-door gate for a turn with no request must not claim a pasted document was dropped."""
    from core.agent_runtime.empty_turn import (
        describe_turn_attachments,
        empty_turn_reply,
        turn_carries_retained_document,
    )

    app = _app()
    _, payload = _paste(app)
    doc_id = payload["attachment"]["id"]
    ca.bind_to_turn(session_id=SESSION, turn_id="turn-e", attachment_ids=[doc_id])
    context = {"external_evidence": ca.evidence_items_for_turn(session_id=SESSION, turn_id="turn-e")}
    assert turn_carries_retained_document(context) is True
    reply = empty_turn_reply(attachments=describe_turn_attachments(context), retained=turn_carries_retained_document(context))
    assert "kept with this chat" in reply and "not holding on" not in reply
    # A picked file keeps the reply it always had: its bytes really do go with the turn.
    plain = ca.stage_attachment(session_id=SESSION, declared_name="n.txt", declared_type="text/plain", data=b"x")
    ca.bind_to_turn(session_id=SESSION, turn_id="turn-f", attachment_ids=[plain["id"]])
    plain_context = {"external_evidence": ca.evidence_items_for_turn(session_id=SESSION, turn_id="turn-f")}
    assert turn_carries_retained_document(plain_context) is False
    assert "not holding on" in empty_turn_reply(attachments=describe_turn_attachments(plain_context), retained=False)
    # A client cannot mint the flag: a forged evidence item without an authority id is not a document.
    assert turn_carries_retained_document({"external_evidence": [{"kind": "text", "document": True}]}) is False
