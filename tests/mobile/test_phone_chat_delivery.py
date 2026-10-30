"""Chat delivery identities are verified against the actual served runtime."""
from __future__ import annotations

import secrets

import pytest

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_sealed_transport import action, opened, pair


def send(phone, key, shared, claim, params):
    wire = action(key, shared, claim, "chats.send", params)
    status, response = http(phone, "/api/mobile/companion", wire)
    return status, opened(response, shared, wire)


def ledger_row(desktop, statement, values):
    import sqlite3

    from storage.db import resolve_runtime_db_filename

    _base, _runtime, home = desktop
    path = resolve_runtime_db_filename(home / "data")
    with sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(statement, values).fetchone()
    return row


@pytest.mark.parametrize("desktop", ["full"], indirect=True)
def test_retry_delivers_one_canonical_turn_and_changed_bytes_refuse(desktop):
    phone, key, shared, claim = pair(desktop, rights={"history": True, "attachments": True})
    message = "Phone retry evidence " + secrets.token_hex(8)
    params = {"message": message, "chat_id": "mob-" + secrets.token_hex(10), "idempotency_key": secrets.token_hex(16)}
    status, first = send(phone, key, shared, claim, params)
    assert status == 200, first
    status, again = send(phone, key, shared, claim, params)
    assert status == 200, again
    assert first["result"]["reply"] == again["result"]["reply"]
    assert again["result"]["commit"]["type"] == "response.replay"
    external_id = first["result"]["turn_id"]
    row = ledger_row(desktop, "SELECT COUNT(*) AS count, MIN(principal) AS principal FROM invocation_requests WHERE external_value = ?", (external_id,))
    assert row["count"] == 1
    assert row["principal"] == "channel:http:mobile:" + claim["device_id"]
    status, conflict = send(phone, key, shared, claim, {**params, "message": message + " changed"})
    assert status == 409 and conflict["code"] == "idempotency_conflict"
    wire = action(key, shared, claim, "chats.history", {"chat_id": params["chat_id"]})
    status, history = http(phone, "/api/mobile/companion", wire)
    assert status == 200
    rows = opened(history, shared, wire)["result"]["messages"]
    assert sum(row.get("role") == "user" and row.get("content") == message for row in rows) == 1
    canonical = first["result"]["canonical_chat_id"]
    status, followup = send(phone, key, shared, claim, {"chat_id": canonical,
        "message": "A separate follow-up " + secrets.token_hex(6), "idempotency_key": secrets.token_hex(16)})
    assert status == 200, followup
    assert followup["result"]["canonical_chat_id"] == canonical
    wire = action(key, shared, claim, "chats.list", {"search": message.upper()})
    status, response = http(phone, "/api/mobile/companion", wire)
    assert status == 200
    assert any(row["chat_id"] == canonical for row in opened(response, shared, wire)["result"]["chats"])


def test_cancel_and_progress_are_bound_to_the_calling_phone(desktop):
    from core.runtime_task_events import emit_runtime_event
    from core.web.api.mobile_companion_api import mobile_companion_service
    from core.web.api.turn_cancel import register_turn, unregister_turn

    phone, key, shared, claim = pair(desktop, rights={"history": True, "attachments": True})
    service = mobile_companion_service()
    token = secrets.token_hex(16)
    session, cancel_id = "openclaw:" + secrets.token_hex(10), "mob-" + secrets.token_hex(20)
    service.store.remember_turn(claim["device_id"], token, {"digest": "test", "session_id": session,
                                "cancel_id": cancel_id, "chat_id": "mob-test"})
    event = register_turn(session, cancel_id)
    try:
        emit_runtime_event({"runtime_session_id": session, "cancel_turn_id": cancel_id},
                           event_type="status", message="Checking the requested task.")
        for verb in ["chats.progress", "chats.cancel"]:
            wire = action(key, shared, claim, verb, {"idempotency_key": token})
            status, response = http(phone, "/api/mobile/companion", wire)
            assert status == 200
            value = opened(response, shared, wire)["result"]
            if verb == "chats.progress":
                assert value["events"][0]["message"] == "Checking the requested task."
            else:
                assert value["state"] == "cancelled" and event.is_set()
        _phone, other_key, other_shared, other = pair(desktop, rights={"history": True, "attachments": True})
        for verb in ["chats.progress", "chats.cancel"]:
            wire = action(other_key, other_shared, other, verb, {"idempotency_key": token})
            status, response = http(phone, "/api/mobile/companion", wire)
            assert status == 404
            assert opened(response, other_shared, wire)["code"] == "turn_not_found"
    finally:
        unregister_turn(session, cancel_id)


@pytest.mark.parametrize("desktop", ["full"], indirect=True)
def test_shared_phone_outbox_survives_a_lost_reply_without_another_turn(desktop, tmp_path):
    import hashlib
    import json
    import socket
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from urllib.request import Request, urlopen

    phone, key, shared, claim = pair(desktop, rights={"history": True, "attachments": True})
    wires = []

    class FaultProxy(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            wires.append(raw)
            with urlopen(Request(phone + self.path, data=raw, headers={"Content-Type": "application/json"}), timeout=15) as upstream:
                payload, status = upstream.read(), upstream.status
            if len(wires) == 1:
                # The actual daemon committed its reply; the path loses it.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), FaultProxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        from nacl.signing import VerifyKey

        message = "Lost reply proof " + secrets.token_hex(8)
        credentials = {"baseUrl": f"http://127.0.0.1:{proxy.server_port}", "deviceId": claim["device_id"],
                       "grantId": claim["grant_id"], "grantSecret": claim["grant_secret"],
                       "deviceSeedHex": key.encode().hex(), "desktopFingerprint": claim["desktop_fingerprint"],
                       "pairedAt": "2026-10-20T00:00:00Z", "protocol": "vool.mobile.sealed.v2",
                       "desktopEncryptionKey": VerifyKey(bytes.fromhex(claim["desktop_public_key"])).to_curve25519_public_key().encode().hex()}
        program = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {SealedMobileCompanionClient} from './src/protocol/sealed.ts';
import {MemoryOutboxStore,Outbox} from './src/protocol/outbox.ts';
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const client=new SealedMobileCompanionClient(input.credentials);
const store=new MemoryOutboxStore();
let delivered=0;
const queue=new Outbox(store,client,()=>{},()=>{delivered++;});
const turn=await queue.enqueue(null,input.message);
await queue.flush();
assert.equal((await store.load()).length,1,'lost response must keep the saved message');
// A reconstructed outbox uses the same saved identity after returning to the app.
const resumed=new Outbox(store,client,()=>{},()=>{delivered++;});
await Promise.all([resumed.flush(),resumed.flush()]);
assert.equal((await store.load()).length,0);
assert.equal(delivered,1);
await resumed.flush();
console.log(JSON.stringify({key:turn.key,chat:turn.chatId}));
"""
        result = subprocess.run(["node", "--input-type=module", "-e", program],
                                cwd=Path(__file__).resolve().parents[2] / "mobile",
                                input=json.dumps({"credentials": credentials, "message": message}),
                                text=True, capture_output=True, timeout=30)
        (tmp_path / "outbox-journey.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        facts = json.loads(result.stdout)
        assert len(wires) == 2
        assert all(message.encode() not in wire and claim["grant_secret"].encode() not in wire for wire in wires)
        external = "mob-" + hashlib.sha256((claim["device_id"] + ":" + facts["key"]).encode()).hexdigest()[:40]
        row = ledger_row(desktop, "SELECT COUNT(*) FROM invocation_requests WHERE external_value = ?", (external,))
        assert row[0] == 1
        wire = action(key, shared, claim, "chats.history", {"chat_id": facts["chat"]})
        status, response = http(phone, "/api/mobile/companion", wire)
        assert status == 200
        messages = opened(response, shared, wire)["result"]["messages"]
        assert sum(row.get("role") == "user" and row.get("content") == message for row in messages) == 1
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("desktop", ["full"], indirect=True)
def test_chat_only_phones_get_replies_without_grafting_onto_desktop_or_each_other(desktop):
    phone, key, shared, claim = pair(desktop)
    raw = "mob-" + secrets.token_hex(10)
    status, first = send(phone, key, shared, claim, {"chat_id": raw, "message": "A new private mobile conversation",
        "idempotency_key": secrets.token_hex(16)})
    assert status == 200 and first["result"]["reply"], first
    assert first["result"]["chat_id"] == raw
    canonical = first["result"]["canonical_chat_id"]
    status, forbidden = send(phone, key, shared, claim, {"chat_id": canonical, "message": "Continue this desktop conversation",
        "idempotency_key": secrets.token_hex(16)})
    assert status == 403 and forbidden["code"] == "phone_history_required"
    for verb, params in [("chats.list", {}), ("chats.history", {"chat_id": canonical})]:
        wire = action(key, shared, claim, verb, params)
        assert http(phone, "/api/mobile/companion", wire)[0] == 403
    other_phone, other_key, other_shared, other = pair(desktop)
    status, second = send(other_phone, other_key, other_shared, other, {"chat_id": raw, "message": "Same handle, different phone",
        "idempotency_key": secrets.token_hex(16)})
    assert status == 200, second
    assert second["result"]["canonical_chat_id"] != canonical
    # A follow-up through the phone's own handle still resumes its own thread.
    status, again = send(phone, key, shared, claim, {"chat_id": raw, "message": "A follow-up",
        "idempotency_key": secrets.token_hex(16)})
    assert status == 200 and again["result"]["canonical_chat_id"] == canonical
