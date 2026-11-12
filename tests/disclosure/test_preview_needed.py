"""A caller that cannot show a cloud preview is told at once and never waits.

Only the chat page answers a preview while its turn waits, and it declares so
(``disclosure_review: "wait"``, beside ``on_busy``). Any other caller of the
served chat door -- a script, the CLI, a phone, a channel bridge -- gets a
typed "preview needed" result right away. Nothing is sent, the preview stays
open on the chat page, and only exactly the approved bytes may go out on a
later send. The in-process half proves no thread waits on such a preview.
"""
from __future__ import annotations

import io
import json
import time

import pytest

from core.disclosure import wire
from core.disclosure.sources import SourceCatalog
from core.provider_invocation_gateway import DirectProviderRequest
from core.remote_fetch_policy import remote_fetch_policy_scope
from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import CloudDaemon, _json, _provider_context, _resource

URL = "https://cloud-model.invalid/v1/chat/completions"
BODY = {"model": "outside-scripted", "messages": [{"role": "user", "content": "Summarize the attached file."}]}


def _call(scope: dict, holder: list | None = None):
    catalog = SourceCatalog()
    catalog.add("Summarize the attached file.", "conversation", "*")
    catalog.add("The workshop opens at noon.", "attachment", "att-1")
    request = DirectProviderRequest(trace_id="turn-1", context={}, metadata={}, max_output_tokens=10)
    bound = {"chat_id": "needed-chat", "request_id": "turn-1", "_disclosure_sources": catalog, **scope}
    if holder is not None:
        bound[wire.PENDING_HOLDER_KEY] = holder
    with remote_fetch_policy_scope(bound):
        context = wire.request_context(request, provider="outside-provider", cloud=True)
        return wire.prepare(dict(BODY), url=URL, context=context, request=request)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("core.cloud_transport._operator_cloud_network_allowed", lambda: True)
    yield
    wire.close_chat("needed-chat")


def _told_at_once(holder: list) -> wire.DisclosurePreviewNeeded:
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as Waited

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_call, {}, holder)
    try:
        error = future.exception(timeout=5)
    except Waited:
        for item in wire.previews(chat_id="needed-chat"):
            if item["state"] == "waiting":
                wire.decide(item["id"], item["sha256"], "cancel")
        pytest.fail("an undeclared caller must not wait on the preview")
    finally:
        executor.shutdown(wait=False)
    assert isinstance(error, wire.DisclosurePreviewNeeded), error
    return error


def test_an_undeclared_caller_is_told_at_once_and_only_the_approved_bytes_may_follow(home):
    holder: list = []
    raised = _told_at_once(holder)
    assert isinstance(raised, wire.DisclosureCancelled), "nothing may count as sent"
    pending = raised.preview
    assert holder == [pending]
    assert pending["type"] == "disclosure_preview" and pending["approve_at"] == "chat_page"
    assert pending["destination_host"] == "cloud-model.invalid" and "Open this chat" in pending["detail"]
    [shown] = [p for p in wire.previews(chat_id="needed-chat") if p["id"] == pending["preview_id"]]
    assert shown["state"] == "waiting" and shown["detached"] is True and shown["can_transform"] is False

    # Asked again before any approval: told again, never sent.
    with pytest.raises(wire.DisclosurePreviewNeeded):
        _call({})
    for other in wire.previews(chat_id="needed-chat"):
        if other["id"] != shown["id"] and other["state"] == "waiting":
            wire.decide(other["id"], other["sha256"], "cancel")
    with pytest.raises(ValueError):
        wire.decide(shown["id"], shown["sha256"], "parts", sections=[])
    wire.decide(shown["id"], shown["sha256"], "send")
    sent = _call({})
    assert sent.wire_body == shown["body"].encode("utf-8") and sent.cloud_destination is True

    # A changed request is a new review.
    changed = dict(BODY, messages=[{"role": "user", "content": "Summarize it differently."}])
    catalog = SourceCatalog()
    catalog.add("Summarize it differently.", "conversation", "*")
    catalog.add("The workshop opens at noon.", "attachment", "att-1")
    request = DirectProviderRequest(trace_id="turn-2", context={"items_included": [{"source_type": "runtime_memory"}]},
                                    metadata={}, max_output_tokens=10)
    with remote_fetch_policy_scope({"chat_id": "needed-chat", "request_id": "turn-2", "_disclosure_sources": catalog}):
        context = wire.request_context(request, provider="outside-provider", cloud=True)
        with pytest.raises(wire.DisclosurePreviewNeeded):
            wire.prepare(changed, url=URL, context=context, request=request)


def test_the_declared_page_still_waits_for_its_answer(home):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor() as executor:
        future = executor.submit(_call, {wire.REVIEW_WAITS_KEY: "wait"})
        deadline = time.monotonic() + 10
        waiting = []
        while time.monotonic() < deadline and not waiting:
            waiting = [p for p in wire.previews(chat_id="needed-chat") if p["state"] == "waiting"]
            time.sleep(0.05)
        assert waiting and waiting[0]["detached"] is False and not future.done()
        wire.decide(waiting[0]["id"], waiting[0]["sha256"], "send")
        assert future.result(timeout=10).wire_body == waiting[0]["body"].encode("utf-8")


def test_the_served_door_answers_an_api_caller_at_once_and_sends_nothing(tmp_path):
    raw = []
    provider = rig.CapturingProvider(default="The workshop opens at noon.")
    handler = provider._server.RequestHandlerClass
    original = handler.do_POST

    def capture(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        raw.append(body)
        self.rfile = io.BytesIO(body)
        return original(self)

    handler.do_POST = capture
    daemon = CloudDaemon(tmp_path / "daemon-home", provider=provider, model="disclosure-recording:scripted",
                         provider_name="disclosure-recording")
    with _provider_context(provider):
        daemon.start()
        _resource("daemon", daemon.process.pid, daemon.port, "running")
        try:
            assert daemon.certify(timeout=60).get("state") == "verified"
            raw.clear()
            session = rig.canonical_session("disclosure-preview-needed")
            attachment = daemon.upload(data=b"The workshop opens at noon.\n", name="workshop.txt",
                                       declared_type="text/plain", session_id=session)
            attachment_id = attachment.get("attachment_id") or attachment.get("attachment", {}).get("id") or attachment.get("id")
            started = time.monotonic()
            # Far below the preview's lifetime: a caller that waited would time out here.
            response = daemon.chat("Summarize the attached workshop document.", session_id=session,
                                   attachments=[attachment_id], turn_id="preview-needed", timeout=90,
                                   disclosure_review="")
            assert time.monotonic() - started < 90
            pending = response.get("vool_pending")
            assert pending and pending["type"] == "disclosure_preview" and pending["approve_at"] == "chat_page", response
            assert "Open this chat" in pending["detail"]
            answer = (response.get("message") or {}).get("content") or ""
            assert answer == pending["detail"], "the turn's own answer must say a preview is waiting"
            [entry] = pending["previews"]
            assert entry["chat_id"] == session
            assert raw == [], {"raw": raw, "response": response}
            waiting = [p for p in _json(daemon.base_url + "/api/disclosure/pending")["requests"]
                       if p["id"] == entry["preview_id"]]
            assert waiting and waiting[0]["state"] == "waiting" and waiting[0]["detached"] is True
            assert "workshop" in waiting[0]["body"]
        finally:
            daemon.stop()
            _resource("daemon", daemon.process.pid, daemon.port, "stopped")
