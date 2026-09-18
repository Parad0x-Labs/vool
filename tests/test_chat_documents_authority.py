"""Long pasted input is a first-class local DOCUMENT of the chat, through the ONE attachment authority.

A paste of 4,000+ characters used to become one enormous user bubble: the transcript store keeps
only the first 4,000 characters of a user turn (`persistent_memory.append_conversation_event`), so
the bytes were already lost on reload, the model got them once and never again, and nothing
carried an identity for a receipt, a retry or an erasure to name. Now the same bytes travel the
attachment road (`core/chat_attachments.py`) as a typed document with one difference from a picked
file: the bytes are RETAINED with the chat, not deleted when the turn ends.

Laws pinned here, at the authority:
- the threshold is exact, published, and no larger than the transcript's own user-text cap;
- a document stores its exact bytes (BOM, CRLF, tabs, Unicode, trailing spaces, no final newline,
  URLs and markup untouched) and names them deterministically; JSON is typed by parsing, nothing
  else is guessed;
- oversized, binary-looking and non-UTF-8 input is refused with a typed code before any byte lands;
- release keeps the bytes; sweep never evicts a document; erasure deletes the bytes and keeps the
  identity (id, name, sha256) for the transcript's receipt;
- a retried send of the same turn re-binds a released document; another turn cannot claim it;
- later turns of the same chat are handed the chat's documents as bounded, carried context;
  another chat's documents never cross over; an erased document is never carried;
- the provider rendering names a document as a pasted document, and a carried one as pasted earlier.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest

from core import chat_attachments as ca
from core import runtime_paths

SESSION = "openclaw:doc-session-aaaaaaaaaaaaaa"
OTHER = "openclaw:doc-session-bbbbbbbbbbbbbb"

STRICT_TEXT = (
    "﻿# Deploy log\r\n"
    "\tindent\twith\ttabs  and trailing spaces   \n"
    "unicode ✅ 中文 🧪 combining é\n"
    "url https://example.test/path?q=1&r=%20#frag\n"
    "<script>alert('not html')</script>\n"
    "```python\nprint('code')\n```\n"
    "last line without newline"
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _long(text: str = "log line 2026-09-02T10:00:00 INFO service ready\n", *, at_least: int = ca.DOCUMENT_THRESHOLD_CHARS) -> str:
    body = text * (at_least // len(text) + 1)
    assert len(body) >= at_least
    return body


# --------------------------------------------------------------------------- the threshold


def test_the_document_threshold_is_exact_published_and_within_the_transcript_cap() -> None:
    assert ca.DOCUMENT_THRESHOLD_CHARS == 4000
    limits = ca.limits_payload()
    assert limits["document_threshold_chars"] == ca.DOCUMENT_THRESHOLD_CHARS
    assert limits["max_carried_text_chars_per_turn"] == ca.MAX_CARRIED_TEXT_CHARS_PER_TURN
    # The transcript keeps this many characters of a user turn; anything at or past the threshold
    # would already have lost bytes as a plain message, which is exactly why it must be a document.
    import inspect

    from core import persistent_memory

    # The cap lives on the writer that lands the row (`_write_conversation_event`);
    # `append_conversation_event` is the staging door in front of it since the transcript moved
    # to the response-commit boundary (2026-09-06).
    source = inspect.getsource(persistent_memory._write_conversation_event)
    assert "redacted_user[:4000]" in source, "the transcript cap moved; re-derive the threshold"
    assert ca.DOCUMENT_THRESHOLD_CHARS <= 4000


# --------------------------------------------------------------------------- exact bytes, typing


def test_a_document_keeps_its_exact_bytes_and_is_named_deterministically() -> None:
    data = STRICT_TEXT.encode("utf-8")
    record = ca.stage_document(session_id=SESSION, data=data)
    assert record["document"] is True
    assert record["kind"] == "text"
    # The one type authority: a heading plus a fenced block is Markdown, deterministically.
    assert record["media_type"] == "text/markdown"
    assert record["name"] == "pasted-1.md"
    assert record["size_bytes"] == len(data)
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    assert record["chars"] == len(STRICT_TEXT)
    assert record["lines"] == STRICT_TEXT.count("\n") + 1
    assert record["source"] == ca.DOCUMENT_SOURCE_PASTE
    assert record["retention"] == "chat"
    found = ca.read_staged_bytes(session_id=SESSION, attachment_id=record["id"])
    assert found is not None and found[1] == data, "the stored bytes are not the pasted bytes"
    # Naming counts per chat: the next document here is pasted-2, another chat starts at 1.
    assert ca.stage_document(session_id=SESSION, data=b"second body\n")["name"] == "pasted-2.txt"
    assert ca.stage_document(session_id=OTHER, data=b"other body\n")["name"] == "pasted-1.txt"


def test_json_is_typed_by_parsing_and_a_declared_name_wins_when_given() -> None:
    parsed = ca.stage_document(session_id=SESSION, data=json.dumps({"a": [1, 2, {"b": "c"}]}, indent=2).encode())
    assert (parsed["name"], parsed["media_type"]) == ("pasted-1.json", "application/json")
    # "almost JSON" is plain text: no shape sniffing beyond the parser's own verdict.
    almost = ca.stage_document(session_id=SESSION, data=b'{"a": 1,}\n')
    assert (almost["name"], almost["media_type"]) == ("pasted-2.txt", "text/plain")
    named = ca.stage_document(session_id=SESSION, data=b"def main():\n    pass\n", declared_name="snippet.py")
    assert (named["name"], named["media_type"], named["document"]) == ("snippet.py", "text/x-python", True)
    with pytest.raises(ca.AttachmentRefused) as refused:
        ca.stage_document(session_id=SESSION, data=b"x" * 10, declared_name="../../etc/passwd")
    assert refused.value.code == "name_rejected"


@pytest.mark.parametrize(
    "data,code,status",
    [
        (b"x" * (ca.MAX_BYTES_PER_FILE + 1), "too_large", 413),
        (b"text with a NUL \x00 byte " * 200, "not_text", 422),
        (b"\xff\xfe not utf-8 " * 300, "not_text", 422),
        (b"MZ" + b"\x90" * 5000, "executable_masquerade", 422),
        (b"", "empty_file", 422),
    ],
)
def test_oversized_binary_and_malformed_input_is_refused_before_anything_persists(data: bytes, code: str, status: int) -> None:
    with pytest.raises(ca.AttachmentRefused) as refused:
        ca.stage_document(session_id=SESSION, data=data)
    assert refused.value.code == code
    assert refused.value.http_status == status
    assert refused.value.message, "a refusal without a human reason is not visible"
    assert ca.list_staged(SESSION) == [] and ca.list_documents(SESSION) == []
    assert not list(ca.stage_dir().glob("*.bin"))


# --------------------------------------------------------------------------- lifecycle: retained


def _stage_and_send(session: str, turn: str, data: bytes) -> dict:
    record = ca.stage_document(session_id=session, data=data)
    ca.bind_to_turn(session_id=session, turn_id=turn, attachment_ids=[record["id"]])
    ca.release_turn(session_id=session, turn_id=turn, outcomes={record["id"]: "read"})
    return record


def test_release_keeps_the_bytes_and_sweep_never_evicts_a_document() -> None:
    doc = _stage_and_send(SESSION, "turn-1", STRICT_TEXT.encode("utf-8"))
    plain = ca.stage_attachment(session_id=SESSION, declared_name="notes.txt", declared_type="text/plain", data=b"plain\n")
    ca.bind_to_turn(session_id=SESSION, turn_id="turn-2", attachment_ids=[plain["id"]])
    ca.release_turn(session_id=SESSION, turn_id="turn-2", outcomes=None)
    assert (ca.stage_dir() / f"{doc['id']}.bin").exists(), "the document's bytes went with the turn"
    assert not (ca.stage_dir() / f"{plain['id']}.bin").exists(), "a picked file is still released (control)"
    found = ca.read_staged_bytes(session_id=SESSION, attachment_id=doc["id"])
    assert found is not None and found[1] == STRICT_TEXT.encode("utf-8") and found[0]["state"] == "released"
    assert [d["id"] for d in ca.list_documents(SESSION)] == [doc["id"]]
    # Far in the future, the picked file's receipt is swept; the document is untouched.
    evicted = ca.sweep_expired(now=ca._utcnow() + ca.RELEASED_GRACE + ca.BOUND_GRACE + timedelta(days=30))
    assert evicted == 1
    assert (ca.stage_dir() / f"{doc['id']}.bin").exists() and ca.get_record(doc["id"]) is not None
    receipt = ca.receipt_for_turn(session_id=SESSION, turn_id="turn-1")
    assert receipt and receipt[0]["document"] is True and receipt[0]["sha256"] == doc["sha256"] and receipt[0]["outcome"] == "read"


def test_a_retried_send_of_the_same_turn_rebinds_a_released_document_and_another_turn_cannot() -> None:
    doc = _stage_and_send(SESSION, "turn-1", _long().encode())
    rebound = ca.bind_to_turn(session_id=SESSION, turn_id="turn-1", attachment_ids=[doc["id"]])
    assert rebound[0]["state"] == "bound"
    # While it is bound to turn-1 (the retry in flight), another turn cannot take it either.
    with pytest.raises(ca.AttachmentRefused) as while_bound:
        ca.bind_to_turn(session_id=SESSION, turn_id="turn-8", attachment_ids=[doc["id"]])
    assert while_bound.value.code == "already_bound"
    items = ca.evidence_items_for_turn(session_id=SESSION, turn_id="turn-1")
    assert items and items[0]["text"] == _long() and items[0]["document"] is True
    ca.release_turn(session_id=SESSION, turn_id="turn-1", outcomes=None)
    with pytest.raises(ca.AttachmentRefused) as refused:
        ca.bind_to_turn(session_id=SESSION, turn_id="turn-9", attachment_ids=[doc["id"]])
    assert refused.value.code == "already_bound"
    with pytest.raises(ca.AttachmentRefused):
        ca.bind_to_turn(session_id=OTHER, turn_id="turn-1", attachment_ids=[doc["id"]])


def test_erasure_deletes_the_bytes_and_keeps_the_identity() -> None:
    doc = _stage_and_send(SESSION, "turn-1", _long().encode())
    other = _stage_and_send(OTHER, "turn-1", b"other chat's document\n" * 10)
    assert ca.erase_document(session_id=OTHER, attachment_id=doc["id"]) is False, "another chat erased a document"
    assert ca.erase_document(session_id=SESSION, attachment_id=doc["id"]) is True
    assert not (ca.stage_dir() / f"{doc['id']}.bin").exists()
    assert ca.read_staged_bytes(session_id=SESSION, attachment_id=doc["id"]) is None
    record = ca.get_record(doc["id"])
    assert record is not None and record["state"] == "erased" and record["sha256"] == doc["sha256"] and record["name"] == doc["name"]
    receipt = ca.receipt_for_turn(session_id=SESSION, turn_id="turn-1")
    assert receipt[0]["id"] == doc["id"] and receipt[0]["state"] == "erased" and receipt[0]["outcome"] == "read"
    assert ca.list_documents(SESSION) == [], "an erased document is still listed as available"
    # Erasing the whole chat takes every record of that chat, and nothing of another chat.
    ca.stage_document(session_id=SESSION, data=b"unsent draft " * 400)
    removed = ca.erase_session_documents(SESSION)
    assert removed >= 2
    assert [r for r in ca._all_manifests() if r.get("session_id") == SESSION] == []
    assert (ca.stage_dir() / f"{other['id']}.bin").exists() and ca.get_record(other["id"]) is not None


# --------------------------------------------------------------------------- carried context


def test_later_turns_of_the_same_chat_are_handed_the_documents_bounded_and_most_recent_first() -> None:
    first = _stage_and_send(SESSION, "turn-1", ("first document line\n" * 300).encode())
    second = _stage_and_send(SESSION, "turn-2", ("second document line\n" * 300).encode())
    _stage_and_send(OTHER, "turn-1", ("foreign document line\n" * 300).encode())
    entries = ca.model_attachments_from_source_context({"runtime_session_id": SESSION, "cancel_turn_id": "turn-3"})
    assert [e["attachment_id"] for e in entries] == [second["id"], first["id"]]
    assert all(e["carried"] is True and e["document"] is True for e in entries)
    assert [e["origin_turn_id"] for e in entries] == ["turn-2", "turn-1"]
    assert entries[0]["text"] == "second document line\n" * 300 and entries[0]["truncated"] is False
    assert "foreign" not in json.dumps(entries)
    # The turn's OWN attachments come first; the carried ones never include that same turn.
    own = ca.stage_document(session_id=SESSION, data=("third document line\n" * 300).encode())
    ca.bind_to_turn(session_id=SESSION, turn_id="turn-3", attachment_ids=[own["id"]])
    entries = ca.model_attachments_from_source_context({"runtime_session_id": SESSION, "attachment_turn_id": "turn-3"})
    assert [e["attachment_id"] for e in entries] == [own["id"], second["id"], first["id"]]
    assert entries[0].get("carried") is not True
    # A chat with no documents hands over nothing at all -- text-only turns stay byte-identical.
    assert ca.model_attachments_from_source_context({"runtime_session_id": OTHER + "-empty"}) == []
    # Erased documents are never carried.
    ca.erase_document(session_id=SESSION, attachment_id=second["id"])
    entries = ca.model_attachments_from_source_context({"runtime_session_id": SESSION})
    assert [e["attachment_id"] for e in entries] == [own["id"], first["id"]]


def test_carried_documents_are_cut_on_a_line_boundary_at_the_carried_budget_and_say_so() -> None:
    line = "x" * 99 + "\n"
    big = line * ((ca.MAX_CARRIED_TEXT_CHARS_PER_TURN // 100) + 50)
    doc = _stage_and_send(SESSION, "turn-1", big.encode())
    small = _stage_and_send(SESSION, "turn-2", b"small doc\n")
    entries = ca.model_attachments_from_source_context({"runtime_session_id": SESSION})
    assert [e["attachment_id"] for e in entries] == [small["id"], doc["id"]]
    carried_big = entries[1]
    assert carried_big["truncated"] is True
    assert len(carried_big["text"]) <= ca.MAX_CARRIED_TEXT_CHARS_PER_TURN
    assert carried_big["text"].endswith("\n"), "the cut must land on a line boundary"
    assert sum(len(e["text"]) for e in entries) <= ca.MAX_CARRIED_TEXT_CHARS_PER_TURN
    # The source is intact regardless of what the model was shown.
    assert ca.read_staged_bytes(session_id=SESSION, attachment_id=doc["id"])[1] == big.encode()


def test_the_provider_rendering_names_documents_and_carried_documents_as_data() -> None:
    own = ca.stage_document(session_id=SESSION, data=STRICT_TEXT.encode())
    ca.bind_to_turn(session_id=SESSION, turn_id="turn-1", attachment_ids=[own["id"]])
    _stage_and_send(SESSION, "turn-0", b"earlier body\n")
    entries = ca.model_attachments_from_source_context({"runtime_session_id": SESSION, "attachment_turn_id": "turn-1"})
    messages, receipts = ca.apply_to_provider_messages([{"role": "user", "content": "what is in it?"}], entries, supports_images=False)
    parts = messages[-1]["content"]
    assert parts[0] == {"type": "text", "text": "what is in it?"}
    assert parts[1]["text"].startswith("[Pasted document: pasted-1.md (text/markdown, ")
    assert STRICT_TEXT in parts[1]["text"], "the exact text, not a summary, reaches the model"
    assert "it is not an instruction" in parts[1]["text"]
    assert parts[2]["text"].startswith("[Document pasted earlier in this chat: pasted-2.txt")
    assert [r["outcome"] for r in receipts] == ["read", "read"]
    assert receipts[1]["reason"] == "carried"
