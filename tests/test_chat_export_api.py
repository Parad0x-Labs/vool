"""PB05 — the complete-transcript export door on the served API.

Driven through the REAL ``dispatch_get`` (the same dispatcher the starlette app calls), so the
owner-local trust decision, the delegation arm in ``service.py`` and the response shapes are the
ones a browser meets. The transcript is seeded through the production writer
(``append_conversation_event``), never by hand-crafting reader-shaped data — except where a test
needs a row shape the writer cannot produce, and that is stated on the test.

Pinned here: no hidden truncation (the legacy cut-offs are gone), stable snapshot under a
concurrent append, verbatim fidelity (fences, tabs, CRLF, CJK, emoji, tables), availability-gate
parity with ``/api/chat/history``, two-session isolation, typed oversize refusal, opt-in
timestamps, attachment references without bytes, and the preview manifest.
"""

from __future__ import annotations

import contextlib
import json
import types
import urllib.parse

import pytest

from core import runtime_paths
from core.web.api import chat_export_api
from core.web.api import service as api_service

SESSION = "openclaw:eeee00000000000000001"
OTHER = "openclaw:eeee00000000000000002"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> types.SimpleNamespace:
    return types.SimpleNamespace(runtime_version_stamp={})


def _get(path: str, client_host: str = "127.0.0.1"):
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    return api_service.dispatch_get(
        path=parsed.path, query=query, runtime=_rt(), model_name="vool", client_host=client_host
    )


def _export(**params):
    body = {"session": params.pop("session", SESSION)}
    body.update(params)
    qs = urllib.parse.urlencode(body)
    return _get(f"/api/chat/export?{qs}")


def _seed(session: str = SESSION, user: str = "hi", assistant: str = "hello", **source) -> None:
    from core.persistent_memory import append_conversation_event

    ctx = {"surface": "api", "platform": "api"}
    ctx.update(source)
    append_conversation_event(
        session_id=session, user_input=user, assistant_output=assistant, source_context=ctx
    )


# --- governed payload minting (the a8 freeze suite's production seams) ---------


@contextlib.contextmanager
def _request_scope(request_id):
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context

    token = set_request_context(request_id)
    try:
        yield
    finally:
        _CURRENT_REQUEST_ID.reset(token)


def _admit_finalize(text: str, request_id: str = ""):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    turn = f"export-t-{abs(hash(text)) % 100000}"
    admit_semantic_result({"response": text, "route_reason": "model_lane"}, turn_id=turn)
    with _request_scope(request_id):
        return finalize_answer(turn_id=turn, canonical_content=text)


# --------------------------------------------------------------------------- #
# The door: ownership, params, existence
# --------------------------------------------------------------------------- #


def test_export_is_owner_local_only() -> None:
    _seed()
    response = _get(f"/api/chat/export?session={urllib.parse.quote(SESSION)}", client_host="10.9.8.7")
    assert response.status == 403
    assert json.loads(response.body)["error"] == "owner_local_required"


def test_export_requires_a_session() -> None:
    response = _get("/api/chat/export")
    assert response.status == 400
    assert json.loads(response.body)["error"] == "session_required"


def test_export_rejects_unknown_format() -> None:
    response = _export(format="unknown-format")
    assert response.status == 400
    assert json.loads(response.body)["error"] == "unknown_format"


def test_export_unknown_session_is_a_typed_404() -> None:
    response = _export(session="openclaw:ffffffffffffffffffff")
    assert response.status == 404
    assert json.loads(response.body)["error"] == "session_not_found"


def test_export_deleted_session_is_not_found() -> None:
    from core.context_namespace import ensure_chat_namespace, set_chat_namespace_state

    _seed()
    ensure_chat_namespace(SESSION)
    set_chat_namespace_state(SESSION, "deleted")
    response = _export()
    assert response.status == 404


# --------------------------------------------------------------------------- #
# The whole transcript, not a window of it
# --------------------------------------------------------------------------- #


def test_export_carries_every_message_past_the_legacy_cut() -> None:
    for i in range(60):
        _seed(user=f"question number {i}", assistant=f"answer number **{i}**")
    response = _export()
    assert response.status == 200
    body = response.body.decode("utf-8")
    assert "Messages: 120 in 60 turns" in body
    # Both ends of a conversation longer than every legacy cap: first turn and last.
    assert "question number 0" in body
    assert "answer number **0**" in body
    assert "question number 59" in body
    assert "answer number **59**" in body
    assert "no message truncation" in body


def test_export_collects_under_a_stable_snapshot(monkeypatch) -> None:
    """A turn appended after the snapshot was captured must not tear the export."""
    for i in range(3):
        _seed(user=f"before {i}", assistant=f"before answer {i}")
    original_scan = chat_export_api.scan_snapshot

    def scan_then_append(session_id):
        result = original_scan(session_id)
        # A concurrent turn lands after the capture (and its gating) was taken.
        _seed(user="a concurrent message", assistant="a concurrent answer")
        return result

    monkeypatch.setattr(chat_export_api, "scan_snapshot", scan_then_append)
    response = _export()
    assert response.status == 200
    body = response.body.decode("utf-8")
    assert "a concurrent message" not in body
    assert "a concurrent answer" not in body
    assert "Messages: 6 in 3 turns" in body


# --------------------------------------------------------------------------- #
# Amendment 1: snapshot truth — one bounded immutable capture, counts == contents
# --------------------------------------------------------------------------- #


def test_trim_between_scan_and_format_cannot_change_the_export(monkeypatch) -> None:
    """The reproduced defect: production trim_jsonl_file rewrites the retained half between
    the counting pass and the formatting pass. The export must deliver and declare exactly
    what the snapshot captured — a row count across reopened mutable files is not a snapshot."""
    from core.memory.files import conversation_log_path, trim_jsonl_file

    for i in range(4):
        _seed(user=f"trim case q{i}", assistant=f"trim case a{i}")
    original_scan = chat_export_api.scan_snapshot

    def scan_then_trim(session_id):
        result = original_scan(session_id)
        # The production trim primitive itself: keeps rows[len//2:], rewriting the file.
        trim_jsonl_file(conversation_log_path(), max_bytes=conversation_log_path().stat().st_size - 1)
        return result

    monkeypatch.setattr(chat_export_api, "scan_snapshot", scan_then_trim)
    response = _export()
    assert response.status == 200
    body = response.body.decode("utf-8")
    assert "trim case q0" in body and "trim case a0" in body
    assert "trim case q3" in body and "trim case a3" in body
    assert "Messages: 8 in 4 turns" in body
    assert "Complete: yes" in body


def test_counts_and_completeness_match_delivered_contents() -> None:
    """Whatever the preamble and the preview declare, the body must deliver — and only that."""
    _seed(user="match q1", assistant="match a1")
    _seed(user="match q2", assistant="match a2")
    body = _export().body.decode("utf-8")
    preview = json.loads(_export(preview="1").body)
    user_sections = body.count("\n## User")
    assistant_sections = body.count("\n## Assistant")
    assert user_sections == preview["user_messages"] == 2
    assert assistant_sections == preview["assistant_messages"] == 2
    assert f"Messages: {user_sections + assistant_sections} in {preview['turns']} turns" in body


def test_malformed_blank_and_invalid_rows_are_counted_not_lost() -> None:
    """Corrupt rows are disclosed, never silently dropped and never silently served."""
    _seed(user="before garbage", assistant="before garbage answer")
    from core.memory.files import conversation_log_path

    with conversation_log_path().open("ab") as handle:
        handle.write(b"not json at all\n")
        handle.write(b"\n")
        handle.write(b"\xff\xfe broken bytes\n")
    _seed(user="after garbage", assistant="after garbage answer")
    preview = json.loads(_export(preview="1").body)
    assert preview["unreadable_rows"] == 3
    body = _export().body.decode("utf-8")
    assert "Unreadable log rows skipped and disclosed: 3" in body
    assert "before garbage" in body and "after garbage" in body


def test_size_cap_covers_the_preamble_and_headers(monkeypatch) -> None:
    """The byte ceiling counts the whole delivered file — preamble included — so the cap can
    never produce a file larger than it admits."""
    for i in range(3):
        _seed(user=f"cap q{i}", assistant=f"cap a{i}")
    monkeypatch.setattr(chat_export_api, "MAX_EXPORT_BYTES", 1 << 30)
    total = len(_export().body)
    monkeypatch.setattr(chat_export_api, "MAX_EXPORT_BYTES", total - 1)
    refused = _export()
    assert refused.status == 413
    assert json.loads(refused.body)["error"] == "export_too_large"
    monkeypatch.setattr(chat_export_api, "MAX_EXPORT_BYTES", total)
    served = _export()
    assert served.status == 200
    assert len(served.body) == total


def test_capture_over_the_bound_is_a_typed_refusal(monkeypatch) -> None:
    """A capture beyond the bounded snapshot is refused, never truncated."""
    _seed(user="capture bound", assistant="capture bound answer")
    monkeypatch.setattr(chat_export_api, "MAX_CAPTURE_BYTES", 10)
    response = _export()
    assert response.status == 413
    assert json.loads(response.body)["error"] == "snapshot_too_large"


# --------------------------------------------------------------------------- #
# Amendment 2: the capture bound limits ALLOCATION, not just acceptance
# --------------------------------------------------------------------------- #


def _write_raw_log(data: bytes) -> None:
    from core.memory.files import conversation_log_path

    path = conversation_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_capture_read_request_is_bounded(monkeypatch) -> None:
    """The filesystem read REQUEST itself must be bounded (≤ cap+1): a whole-file read
    (size −1) allocates before the bound can refuse — the defect this amendment fixes.
    Instrumented at the real pathlib.Path.open seam (which both open().read() and
    Path.read_bytes() go through); the wrapper records every requested size and every
    returned length."""
    import pathlib

    cap = 16
    monkeypatch.setattr(chat_export_api, "MAX_CAPTURE_BYTES", cap)
    _write_raw_log(b"x" * 64)  # over the cap: the refusal path
    recorded: list[tuple[int, int]] = []
    real_open = pathlib.Path.open

    def recording_open(self, *args, **kwargs):
        inner = real_open(self, *args, **kwargs)

        class _Recording:
            def read(self, size=-1):
                data = inner.read(size)
                recorded.append((size, len(data)))
                return data

            def write(self, data):
                return inner.write(data)

            def flush(self):
                return inner.flush()

            def __enter__(self):
                inner.__enter__()
                return self

            def __exit__(self, *exc):
                return inner.__exit__(*exc)

            def close(self):
                return inner.close()

            def readable(self):
                return inner.readable()

        return _Recording()

    monkeypatch.setattr(pathlib.Path, "open", recording_open)
    with pytest.raises(chat_export_api.ExportRefused) as excinfo:
        chat_export_api.capture_log_bytes()
    assert excinfo.value.error == "snapshot_too_large"
    assert recorded, "no filesystem read observed — the instrument did not see the capture"
    unbounded = [req for req, _ret in recorded if req == -1 or req > cap + 1]
    assert not unbounded, f"unbounded read request(s) observed: {unbounded}"
    assert all(ret <= cap + 1 for _req, ret in recorded), f"over-bound reads returned: {recorded}"

    # The accepted path is bounded too: under-cap content must be read with bounded requests.
    recorded.clear()
    _write_raw_log(b"y" * 8)
    assert chat_export_api.capture_log_bytes() == b"y" * 8
    assert recorded, "no filesystem read observed for the accepted capture"
    assert not [req for req, _ret in recorded if req == -1 or req > cap + 1]


def test_capture_size_boundaries(monkeypatch) -> None:
    """empty / cap-1 / cap are captured whole; cap+1 and larger are typed refusals; growth
    between captures is observed by the next request (no stale cached capture)."""
    cap = 16
    monkeypatch.setattr(chat_export_api, "MAX_CAPTURE_BYTES", cap)
    for payload, accepted in [
        (b"", True),                 # empty log
        (b"r" * (cap - 1), True),    # cap-1
        (b"r" * cap, True),          # exactly cap
        (b"r" * (cap + 1), False),   # cap+1
        (b"r" * (cap * 4), False),   # larger than cap
    ]:
        _write_raw_log(payload)
        if accepted:
            assert chat_export_api.capture_log_bytes() == payload
        else:
            with pytest.raises(chat_export_api.ExportRefused) as excinfo:
                chat_export_api.capture_log_bytes()
            assert excinfo.value.error == "snapshot_too_large"
    # Growth after an accepted capture: the next request observes the new reality.
    _write_raw_log(b"r" * (cap - 1))
    assert chat_export_api.capture_log_bytes() == b"r" * (cap - 1)
    _write_raw_log(b"r" * (cap + 5))
    with pytest.raises(chat_export_api.ExportRefused):
        chat_export_api.capture_log_bytes()


def test_capture_refusal_metadata_is_truthful_lower_bound(monkeypatch) -> None:
    """A refusal's size metadata describes what a bounded read OBSERVED — a lower bound on the
    file's size, explicitly incomplete — never a pretended exact whole-file length."""
    cap = 16
    monkeypatch.setattr(chat_export_api, "MAX_CAPTURE_BYTES", cap)
    _write_raw_log(b"r" * (cap * 10))  # far more than any bounded read will observe
    with pytest.raises(chat_export_api.ExportRefused) as excinfo:
        chat_export_api.capture_log_bytes()
    detail = excinfo.value.detail
    assert detail["min_file_bytes"] == cap + 1
    assert detail["observed_complete"] is False
    assert "log_bytes" not in detail  # the exact-length label must not come back


def test_capture_missing_log_is_empty() -> None:
    """No log at all captures as empty (the door then answers 404) — unchanged behavior."""
    assert chat_export_api.capture_log_bytes() == b""


def test_export_two_session_isolation() -> None:
    _seed(session=SESSION, user="alpha for one", assistant="answer for one")
    _seed(session=OTHER, user="beta for two", assistant="answer for two")
    _seed(session=SESSION, user="gamma for one", assistant="answer for one two")
    body = _export().body.decode("utf-8")
    assert "alpha for one" in body and "gamma for one" in body
    assert "beta for two" not in body
    other_body = _export(session=OTHER).body.decode("utf-8")
    assert "beta for two" in other_body
    assert "alpha for one" not in other_body and "gamma for one" not in other_body


# --------------------------------------------------------------------------- #
# Fidelity: the source survives byte for byte
# --------------------------------------------------------------------------- #

RICH_MARKDOWN = (
    "A fenced sample with a nested backtick fence inside a comment:\n"
    "```python\n"
    "\tdef f():\n"
    "        return 'quote ` backtick'\n"
    "```\n"
    "\n"
    "| Item | Count |\n"
    "|---|---|\n"
    "| 阿贝 | 2 |\n"
    "\n"
    "CJK: 日本語のテキスト · emoji: 🎉🤖 · tab\tseparated\r\n"
    "trailing crlf line\r\n"
)


def test_export_preserves_fences_tables_unicode_tabs_and_crlf() -> None:
    _seed(user="paste **the rich one**", assistant=RICH_MARKDOWN)
    body = _export().body.decode("utf-8")
    assert "\tdef f():" in body
    assert "quote ` backtick" in body
    assert "| 阿贝 | 2 |" in body
    assert "日本語のテキスト" in body
    assert "🎉🤖" in body
    assert "tab\tseparated" in body
    # Interior CRLF survives byte for byte. (A message's trailing edge is trimmed by the
    # WRITE authority before anything is persisted — the store never held it — so the
    # export's fidelity contract is "the persisted source, verbatim", which is exactly
    # what the transcript reader serves.)
    assert "tab\tseparated\r\ntrailing crlf line" in body
    # Raw markdown decoration is exported as the source, not as rendered prose.
    assert "paste **the rich one**" in body


def test_export_rows_match_the_transcript_reader() -> None:
    """Availability-gate parity with /api/chat/history, pinned row for row.

    Content compares on trimmed truth: the transcript reader trims for display, while
    the export carries the stored source verbatim (fidelity test above pins that).
    """
    for i in range(5):
        _seed(user=f"q {i}", assistant=f"a **{i}**")
    from urllib.parse import parse_qs

    history = api_service.dispatch_get(
        path="/api/chat/history", query=parse_qs("session=" + SESSION), runtime=_rt(), model_name="vool", client_host="127.0.0.1"
    )
    served = json.loads(history.body)["messages"]
    turns, _counts = chat_export_api.scan_snapshot(SESSION)
    assert [t.user.strip() for t in turns] == [m["content"] for m in served if m["role"] == "user"]
    assert [t.assistant.strip() for t in turns] == [m["content"] for m in served if m["role"] == "assistant"]


def test_withheld_payload_is_never_exported() -> None:
    request_id = "req-export-withheld-0001"
    secret_answer = "the withheld answer bytes"
    commit = _admit_finalize(secret_answer, request_id=request_id)
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    assert set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD, reason="export-test")
    with _request_scope(request_id):
        _seed(user="ask for the secret", assistant=secret_answer)
    response = _export()
    assert response.status == 200
    body = response.body.decode("utf-8")
    assert secret_answer not in body
    assert "unavailable by privacy policy" in body
    preview = json.loads(_export(preview="1").body)
    assert preview["unavailable_by_policy"] == 1
    # Parity: the transcript reader serves the same truth.
    from urllib.parse import parse_qs

    history = api_service.dispatch_get(
        path="/api/chat/history", query=parse_qs("session=" + SESSION), runtime=_rt(), model_name="vool", client_host="127.0.0.1"
    )
    served = json.loads(history.body)["messages"]
    assert all(secret_answer != m["content"] for m in served if m["role"] == "assistant")


# --------------------------------------------------------------------------- #
# Controls: timestamps, attachments, formats
# --------------------------------------------------------------------------- #


def test_timestamps_are_opt_in_and_honest() -> None:
    _seed(user="when was this", assistant="just now **really**")
    default_body = _export().body.decode("utf-8")
    assert "turn completed" not in default_body
    assert "Timestamps: off" in default_body
    stamped = _export(timestamps="1").body.decode("utf-8")
    # The persisted completion time is shown, on the assistant side only — the store has
    # no separately-recorded user send instant, and none may be invented for it.
    assert stamped.count("turn completed") == 1
    assert "## Assistant (turn completed" in stamped
    assert "## User (turn completed" not in stamped
    assert "Timestamps: assistant-side completion times only" in stamped


def test_attachment_references_without_bytes() -> None:
    from core import chat_attachments

    record = chat_attachments.stage_attachment(
        session_id=SESSION, declared_name="notes-week1.txt", declared_type="text/plain", data=b"attached words"
    )
    # The production receipt path: stage -> bind to the turn -> the delivery outcome ->
    # the transcript event carrying the turn id the receipt is filed under.
    chat_attachments.bind_to_turn(session_id=SESSION, turn_id="turn-export-att-1", attachment_ids=[record["id"]])
    chat_attachments.record_delivery(
        session_id=SESSION,
        turn_id="turn-export-att-1",
        receipts=[{"attachment_id": record["id"], "outcome": "read"}],
    )
    _seed(user="here is my file", assistant="got **it**", attachment_turn_id="turn-export-att-1")
    body = _export().body.decode("utf-8")
    assert "Attachment reference: notes-week1.txt" in body
    assert "outcome: read" in body
    assert "attached words" not in body  # never the bytes
    assert "never the file bytes" in body
    without = _export(no_attachments="1").body.decode("utf-8")
    assert "Attachment reference: notes-week1.txt" not in without
    assert "Attachment references: excluded at your request." in without


def test_txt_and_md_formats_and_disposition() -> None:
    _seed(user="format probe", assistant="answer **one**")
    md = _export(format="md")
    assert md.status == 200
    assert md.content_type == "text/markdown; charset=utf-8"
    assert "## User" in md.body.decode("utf-8")
    assert "attachment" in md.headers["Content-Disposition"]
    assert md.headers["Content-Disposition"].rstrip('"').endswith('.md"') or "filename*=UTF-8''" in md.headers["Content-Disposition"]
    txt = _export(format="txt")
    assert txt.content_type == "text/plain; charset=utf-8"
    txt_body = txt.body.decode("utf-8")
    assert "USER" in txt_body and "ASSISTANT" in txt_body
    assert "answer **one**" in txt_body
    assert md.headers["Cache-Control"] == "private, no-store"
    assert txt.headers["X-Content-Type-Options"] == "nosniff"


def test_artifact_rows_are_labelled_not_hidden() -> None:
    """An artifact event (assistant-authored card) is part of the transcript."""
    from core.persistent_memory import append_assistant_artifact_event

    _seed(user="convene the council", assistant="the council answered")
    append_assistant_artifact_event(
        session_id=SESSION,
        artifact_kind="council_verdict",
        artifact={"title": "Verdict"},
        text="VERDICT: the claim is supported",
    )
    body = _export().body.decode("utf-8")
    assert "artifact card: council_verdict" in body
    assert "VERDICT: the claim is supported" in body


# --------------------------------------------------------------------------- #
# Oversize: a typed refusal with counts, never a quiet partial file
# --------------------------------------------------------------------------- #


def test_oversize_export_refuses_with_counts_and_preview_survives(monkeypatch) -> None:
    """Real bytes over the real cap (the cap is temporarily small so the run stays fast;
    the cap semantics exercised are the production ones)."""
    for i in range(12):
        _seed(user=f"big question {i} ", assistant="x" * 700)
    monkeypatch.setattr(chat_export_api, "MAX_EXPORT_BYTES", 6000)
    response = _export()
    assert response.status == 413
    payload = json.loads(response.body)
    assert payload["error"] == "export_too_large"
    assert payload["detail"]["max_bytes"] == 6000
    assert payload["detail"]["message_count"] > 0
    preview = json.loads(_export(preview="1").body)
    assert preview["complete"] is True
    assert preview["message_count"] == 24


# --------------------------------------------------------------------------- #
# Preview manifest
# --------------------------------------------------------------------------- #


def test_preview_manifest_discloses_contents_and_exclusions() -> None:
    _seed(user="one", assistant="two")
    _seed(user="three", assistant="four")
    preview = json.loads(_export(preview="1").body)
    assert preview["ok"] is True
    assert preview["message_count"] == 4
    assert preview["turns"] == 2
    assert preview["complete"] is True
    assert preview["page_size"] == chat_export_api.PAGE_SIZE
    assert preview["source"].startswith("persisted conversation transcript")
    assert "model reasoning" in preview["excluded"]
    assert "tool/activity traces" in preview["excluded"]
