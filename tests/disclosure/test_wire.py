from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from adapters.base_adapter import ModelRequest
from core import provider_http
from core.disclosure.wire import (
    REVIEW_WAITS_KEY,
    DisclosureCancelled,
    DisclosureRefused,
    close_chat,
    decide,
    previews,
)
from core.provider_invocation_gateway import seal_provider_invocation
from core.remote_fetch_policy import remote_fetch_policy_scope


@pytest.fixture
def cloud():
    from core.cloud_escalation_policy import set_free_cloud_enabled

    set_free_cloud_enabled(True)
    bodies = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"received"}}]}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    resource_log = os.environ.get("VOOL_TEST_RESOURCE_LOG")
    if resource_log:
        with open(resource_log, "a") as stream:
            stream.write(json.dumps({"kind": "fake-provider", "pid": os.getpid(), "port": server.server_port, "state": "running"}) + "\n")
    try:
        yield f"http://127.0.0.1:{server.server_port}/chat/completions", bodies
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        if resource_log:
            with open(resource_log, "a") as stream:
                stream.write(json.dumps({"kind": "fake-provider", "pid": os.getpid(), "port": server.server_port, "state": "stopped"}) + "\n")
        close_chat("wire-chat")


def _send(url, text, *, turn="wire-turn", sensitive=True):
    request = ModelRequest("answer", text, context={"chat_id": "wire-chat"}, trace_id=turn,
                           metadata={"_disclosure_sensitive": sensitive})
    payload = {"model": "recording-provider", "messages": [{"role": "user", "content": text}],
               "tools": [{"type": "function", "function": {"name": "read", "description": "Read a section"}}]}
    # This test answers its previews while the call waits, as the chat page does, so it declares it.
    with remote_fetch_policy_scope({REVIEW_WAITS_KEY: "wait"}):
        permit = seal_provider_invocation(request=request, provider_id="recording-provider", model_id="recording-provider",
                                          operation="chat", payload=payload, destination_url=url, cloud_destination=True)
        response = provider_http.post(url, json=permit.consume(), timeout=5)
    return response.json()


def _pending():
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        found = [p for p in previews(chat_id="wire-chat") if p["state"] == "waiting"]
        if found:
            return found[-1]
        time.sleep(0.02)
    raise AssertionError("production gateway did not create a preview")


def test_gateway_and_worker_send_exact_reviewed_utf8_bytes(cloud):
    url, bodies = cloud
    with ThreadPoolExecutor() as executor:
        future = executor.submit(_send, url, "Résumé: Živilė\n<section> & image data")
        preview = _pending()
        assert bodies == []
        assert json.loads(preview["body"])["tools"][0]["function"]["name"] == "read"
        decide(preview["id"], preview["sha256"], "send")
        assert future.result(timeout=8)["choices"][0]["message"]["content"] == "received"
    assert bodies == [preview["body"].encode("utf-8")]
    assert hashlib.sha256(bodies[0]).hexdigest() == preview["sha256"]


def test_changed_retry_reasks_and_cancel_prevents_any_second_send(cloud):
    url, bodies = cloud
    with ThreadPoolExecutor() as executor:
        first = executor.submit(_send, url, "First document")
        preview = _pending()
        decide(preview["id"], preview["sha256"], "send")
        first.result(timeout=8)
        second = executor.submit(_send, url, "First document plus a tool result")
        changed = _pending()
        assert changed["sha256"] != preview["sha256"]
        assert len(bodies) == 1
        with pytest.raises(ValueError, match="changed"):
            decide(changed["id"], preview["sha256"], "send")
        decide(changed["id"], changed["sha256"], "cancel")
        with pytest.raises(DisclosureCancelled, match="Nothing was sent"):
            second.result(timeout=8)
    assert len(bodies) == 1


def test_dont_send_makes_zero_provider_calls(cloud):
    url, bodies = cloud
    with ThreadPoolExecutor() as executor:
        result = executor.submit(_send, url, "Private document")
        preview = _pending()
        decide(preview["id"], preview["sha256"], "cancel")
        with pytest.raises(DisclosureCancelled):
            result.result(timeout=8)
    assert bodies == []


def test_changed_body_or_destination_is_refused_before_transport(cloud):
    from core.disclosure.wire import WirePayload, encode_body

    url, bodies = cloud
    original = {"messages": [{"role": "user", "content": "approved"}]}
    bound = WirePayload(original, encode_body(original), url)
    bound["messages"] = [{"role": "user", "content": "unreviewed"}]
    with pytest.raises(DisclosureRefused, match="changed"):
        provider_http.post(url, json=bound, timeout=5)
    with pytest.raises(DisclosureRefused, match="changed"):
        provider_http.post(url + "/other", json=WirePayload(original, encode_body(original), url), timeout=5)
    assert bodies == []


def test_public_calls_without_turn_ids_do_not_share_approval_state(cloud):
    url, bodies = cloud
    assert _send(url, "First public question", turn=None, sensitive=False)
    assert _send(url, "Another public question", turn=None, sensitive=False)
    assert len(bodies) == 2
    assert not [p for p in previews(chat_id="wire-chat") if p["state"] == "waiting"]


def test_same_body_at_a_different_destination_needs_a_new_review(cloud):
    url, bodies = cloud
    with ThreadPoolExecutor() as executor:
        first = executor.submit(_send, url, "Same body, two destinations")
        preview = _pending()
        decide(preview["id"], preview["sha256"], "send")
        first.result(timeout=8)
        second = executor.submit(_send, url + "?other-destination", "Same body, two destinations")
        changed = _pending()
        assert changed["sha256"] == preview["sha256"]
        assert changed["destination"] != preview["destination"]
        assert len(bodies) == 1
        decide(changed["id"], changed["sha256"], "cancel")
        with pytest.raises(DisclosureCancelled):
            second.result(timeout=8)
    assert len(bodies) == 1


def test_local_network_uses_the_existing_local_only_endpoint_authority():
    from types import SimpleNamespace

    from core.disclosure.wire import encode_body, prepare

    payload = {"model": "local-workshop", "messages": [{"role": "user", "content": "private text"}]}
    request = SimpleNamespace(metadata={})
    # Classification only: no socket or local model is started by this check.
    url = "http://192.168.20.30:18080/api/chat"
    wire = prepare(payload, url=url, request=request, context={"cloud": False})
    assert wire.consume_wire(url) == encode_body(payload)


def test_ascii_review_matches_real_prepaid_envelope_and_transport_without_credentials(cloud):
    from urllib.parse import urlsplit

    from core.disclosure.wire import encode_ascii_body, prepare
    from core.usepod.descriptor import TransportMode, WireProtocol, x402_target
    from core.usepod.transport import UsePodHttpTransport, seal_request_envelope

    endpoint, bodies = cloud
    parsed = urlsplit(endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    url = origin + x402_target(origin=origin, protocol=WireProtocol.OPENAI).path_template
    payload = {"model": "recording-provider", "max_tokens": 100,
               "messages": [{"role": "user", "content": "Résumé: Živilė"}]}
    request = ModelRequest("answer", "question", metadata={})
    context = {"cloud": True, "chat_id": "wire-chat", "turn_id": "ascii-turn", "provider": "recording-provider",
               "has_files": True, "sensitive": True, "attachments": (), "review_waits": True}
    with ThreadPoolExecutor() as executor:
        future = executor.submit(prepare, payload, url=url, request=request, context=context, encoder=encode_ascii_body)
        preview = _pending()
        assert bodies == []
        decide(preview["id"], preview["sha256"], "send")
        wire = future.result(timeout=8)
    envelope = seal_request_envelope(payload=wire, transport_mode=TransportMode.X402, protocol=WireProtocol.OPENAI,
                                     origin=origin, model_id="recording-provider", route_approval_id="reviewed-fake-outside")
    assert envelope.body == wire.consume_wire(url) == preview["body"].encode()
    # The outside server responds without billing or a payment challenge. This
    # exercises the real transport only; no wallet, key, signer or paid call.
    response = UsePodHttpTransport().post_envelope(envelope, origin=origin, token=None, read_timeout_seconds=5)
    try:
        assert response.status == 200
    finally:
        response.close()
    assert bodies == [envelope.body]
    assert b"\\u017d" in bodies[0]


def test_replacement_covers_system_and_tool_description_but_never_protocol_identifiers():
    from core.contacts import store
    from core.disclosure.wire import prepare
    from storage.migrations import run_migrations

    run_migrations()
    store.create_contact(display_name="Text", actor=store.ACTOR_OWNER)
    request = ModelRequest("answer", "question", metadata={})
    payload = {"model": "recording-provider", "system": "Text uses mira@example.invalid.",
               "messages": [{"role": "user", "content": [{"type": "text", "text": "Text: mira@example.invalid."}]}],
               "tools": [{"type": "function", "function": {"name": "text", "description": "Email mira@example.invalid."}}]}
    context = {"cloud": True, "chat_id": "wire-chat", "turn_id": "protocol-turn", "provider": "recording-provider",
               "has_files": True, "sensitive": True, "attachments": (), "review_waits": True}
    with ThreadPoolExecutor() as executor:
        future = executor.submit(prepare, payload, url="http://127.0.0.1:9/model", request=request, context=context)
        first = _pending()
        decide(first["id"], first["sha256"], "replace")
        second = _pending()
        shown = json.loads(second["body"])
        assert shown["messages"][0]["content"][0]["type"] == "text"
        assert shown["tools"][0]["function"]["name"] == "text"
        assert "mira@example.invalid" not in second["body"]
        assert "Text" not in shown["system"] and "Text" not in shown["messages"][0]["content"][0]["text"]
        decide(second["id"], second["sha256"], "send")
        assert future.result(timeout=8)
    close_chat("wire-chat")


@pytest.mark.parametrize("erase", ["forget", "delete"])
def test_approved_bytes_cannot_be_sent_after_the_chat_privacy_epoch_changes(cloud, tmp_path, monkeypatch, erase):
    from core.context_namespace import ensure_chat_namespace, set_chat_namespace_state
    from core.disclosure.wire import prepare
    from core.memory.revocation import begin, complete, initialize_memory_home

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    initialize_memory_home()
    ensure_chat_namespace("wire-chat", project_id="", grant_confirmed_profile=True)
    url, bodies = cloud
    payload = {"model": "recording-provider", "messages": [{"role": "user", "content": "Cobalt Workshop"}]}
    request = ModelRequest("answer", "question", metadata={})
    context = {"cloud": True, "chat_id": "wire-chat", "turn_id": "erase-before-write", "provider": "recording-provider",
               "has_files": True, "sensitive": True, "attachments": (), "review_waits": True}
    with ThreadPoolExecutor() as executor:
        future = executor.submit(prepare, payload, url=url, request=request, context=context)
        preview = _pending()
        decide(preview["id"], preview["sha256"], "send")
        wire = future.result(timeout=8)
    if erase == "forget":
        operation_id = begin("wire-chat", "Cobalt Workshop")
        complete(operation_id)
    else:
        set_chat_namespace_state("wire-chat", "deleted")
    with pytest.raises(DisclosureRefused, match="privacy state changed"):
        provider_http.post(url, json=wire, timeout=5)
    assert bodies == []
    assert previews(chat_id="wire-chat") == []
