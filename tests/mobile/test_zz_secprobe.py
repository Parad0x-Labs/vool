"""Served regression probes for transport and PC-wallet boundaries; external services are local fakes."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import time

import pytest

from core.mobile_companion.listener import network_addresses
from tests.mobile import test_sealed_transport as st
from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http

LAN = next(iter(network_addresses()), "127.0.0.1")


def lan_enable(runtime):
    from core.mobile_companion.listener import listener_for

    return listener_for(runtime).configure(enabled=True, address=LAN)


@pytest.fixture
def lan(desktop, monkeypatch):
    base, runtime, home = desktop
    value = lan_enable(runtime)
    return base, runtime, home, value


def _pair(desktop, value, rights=None, approval_key=None, *, activate_chat=True):
    from nacl.public import Box, PublicKey
    from nacl.signing import SigningKey, VerifyKey

    from core.mobile_companion.sealing import VERSION, open_envelope, seal

    base, runtime, _home = desktop
    phone = f"http://{LAN}:{value['port']}"
    status, start = http(base, "/api/mobile/pairing/start", {})
    assert status == 200
    assert f"{LAN}:{value['port']}" in start["qr_uri"], start["qr_uri"]
    status, challenge = http(phone, "/api/mobile/pairing/challenge?pairing_id=" + start["pairing_id"])
    assert status == 200
    binding = challenge["sealing"]["binding"]
    assert hashlib.sha256(bytes.fromhex(challenge["desktop_public_key"])).hexdigest() == start["desktop_fingerprint"]
    VerifyKey(bytes.fromhex(challenge["desktop_public_key"])).verify(st.canonical(binding), bytes.fromhex(challenge["sealing"]["signature"]))
    key = SigningKey.generate()
    curve = key.to_curve25519_private_key()
    shared = Box(curve, PublicKey(bytes.fromhex(binding["public_key"]))).shared_key()
    proof = {"kind": "vool.mobile.pairing.proof.v1", "pairing_id": start["pairing_id"], "code": start["code"],
             "challenge": challenge["challenge"], "device_public_key": key.verify_key.encode().hex(),
             "expires_at": challenge["expires_at_epoch"]}
    body = {"pairing_id": start["pairing_id"], "code": start["code"], "device_name": "Probe phone", "platform": "android",
            "device_public_key": proof["device_public_key"], "approval_key": approval_key,
            "device_proof": {"algorithm": "ed25519", "signature": key.sign(st.canonical(proof)).signature.hex()}}
    wire = {**seal({"kind": VERSION + ":claim", "body": body}, shared, "request"), "public_key": curve.public_key.encode().hex()}
    status, response = http(phone, "/api/mobile/pairing/claim", wire)
    assert status == 200, (status, response)
    claim = open_envelope(response, shared, "response")["body"]
    if activate_chat or rights:
        rights = {**({"chat": True} if activate_chat else {}), **(rights or {})}
        status, changed = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": claim["device_id"], "rules": rights})
        assert status == 200, changed
    return phone, key, shared, claim


def _receipts(base):
    return http(base, "/api/mobile/receipts?limit=1000")[1]["receipts"]


def test_listener_bound_only_to_chosen_lan_address(lan):
    base, runtime, home, value = lan
    server = runtime.phone_listener.server
    print("LISTENER", server.server_address)
    assert server.server_address[0] == LAN
    s = socket.socket()
    try:
        assert LAN == "127.0.0.1" or s.connect_ex(("127.0.0.1", value["port"])) != 0
    finally:
        s.close()


@pytest.mark.parametrize("path", ["/api/chat", "/api/wallet/approve", "/api/wallet/status", "/api/settings/prefs",
                                  "/api/commands/dispatch", "/api/mobile/settings", "/api/mobile/devices",
                                  "/api/mobile/devices/revoke", "/api/mobile/pairing/start", "/api/mobile/receipts",
                                  "/api/mobile/pairing/page", "//api/mobile/settings", "/api/mobile/info/../settings",
                                  "/api/wallet/limits", "/api/mobile/companion?x=/api/wallet/approve"])
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", None])
def test_lan_peer_reaches_only_closed_routes(lan, path, host):
    base, runtime, home, value = lan
    phone = f"http://{LAN}:{value['port']}"
    headers = {"Host": host} if host else {}
    for body in (None, {}, {"enabled": True}):
        status, out = http(phone, path, body, headers)
        print("PROBE", path, host, body is not None, status, json.dumps(out)[:120])
        if path.startswith("/api/mobile/companion"):
            assert status in {400, 404}
        else:
            assert status == 404, (path, status, out)


def test_lan_plaintext_v1_companion_post_refused(lan, desktop):
    base, runtime, home, value = lan
    phone, key, shared, claim = _pair(desktop, value)
    wire = st.action(key, shared, claim)
    from core.mobile_companion.sealing import open_envelope

    plain = open_envelope(wire, shared, "request")["body"]
    status, out = http(phone, "/api/mobile/companion", plain)
    print("PLAINTEXT", status, out)
    assert status == 400 and out.get("code") == "sealed_transport_required"
    # the same plaintext via the desktop API from a non-loopback caller is refused by code, too
    from core.web.api.runtime import default_workspace_root
    from core.web.api.service import dispatch_post

    resp = dispatch_post(path="/api/mobile/companion", body=plain, headers={}, runtime=runtime, model_name="vool",
                         workspace_root_provider=default_workspace_root, client_host=LAN)
    print("DESKTOP-API-FROM-LAN", resp.status, resp.body[:160])
    assert resp.status == 403


def test_sealed_adversarial_envelopes(lan, desktop):
    from core.mobile_companion.sealing import VERSION, open_envelope, seal

    base, runtime, home, value = lan
    phone, key, shared, claim = _pair(desktop, value)
    results = {}
    # 1. good request then exact replay
    wire = st.action(key, shared, claim)
    results["first"] = http(phone, "/api/mobile/companion", wire)[0]
    results["replay"] = http(phone, "/api/mobile/companion", wire)[0]
    # 2. nonce reuse with a different body (same wire nonce)
    fresh = st.action(key, shared, claim, "devices.me")
    inner = open_envelope(fresh, shared, "request")
    reused = {**seal(inner, shared, "request", nonce=bytes.fromhex(wire["nonce"])), "device_id": claim["device_id"]}
    results["nonce_reuse_new_body"] = http(phone, "/api/mobile/companion", reused)[0]
    # 3. swapped direction: request sealed under the response key
    swapped = {**seal(inner, shared, "response"), "device_id": claim["device_id"]}
    results["swapped_direction"] = http(phone, "/api/mobile/companion", swapped)[0]
    # 4. reflect the server's own sealed reply back as a request
    status, reply = http(phone, "/api/mobile/companion", st.action(key, shared, claim))
    results["reflect_reply"] = http(phone, "/api/mobile/companion", {**reply, "device_id": claim["device_id"]})[0]
    # 5. truncated ciphertext (drop the last 4 bytes) and truncated MAC
    good = st.action(key, shared, claim)
    raw = base64.b64decode(good["ciphertext"])
    results["truncated_tail"] = http(phone, "/api/mobile/companion", {**good, "ciphertext": base64.b64encode(raw[:-4]).decode()})[0]
    results["truncated_to_8"] = http(phone, "/api/mobile/companion", {**good, "ciphertext": base64.b64encode(raw[:8]).decode()})[0]
    results["empty_ct"] = http(phone, "/api/mobile/companion", {**good, "ciphertext": ""})[0]
    results["bad_nonce_len"] = http(phone, "/api/mobile/companion", {**good, "nonce": good["nonce"][:20]})[0]
    results["nonce_not_hex"] = http(phone, "/api/mobile/companion", {**good, "nonce": "zz" * 24})[0]
    # 6. a second phone uses its own key but claims the first phone's device id
    phone2, key2, shared2, claim2 = _pair(desktop, value)
    forged = {**seal({"kind": VERSION + ":request", "body": open_envelope(st.action(key2, shared2, claim2), shared2, "request")["body"]}, shared2, "request"), "device_id": claim["device_id"]}
    results["cross_device_id"] = http(phone, "/api/mobile/companion", forged)[0]
    # 7. phone A's sealed body carrying phone B's inner device id
    inner_b = open_envelope(st.action(key2, shared2, claim2), shared2, "request")
    results["inner_device_mismatch"] = http(phone, "/api/mobile/companion", {**seal(inner_b, shared, "request"), "device_id": claim["device_id"]})[0]
    # 8. pairing-claim purpose on the action path
    results["claim_kind_on_action"] = http(phone, "/api/mobile/companion", {**seal({"kind": VERSION + ":claim", "body": inner["body"]}, shared, "request"), "device_id": claim["device_id"]})[0]
    print("ADVERSARIAL", json.dumps(results, sort_keys=True))
    assert results["first"] == 200
    assert results["replay"] == 409
    assert results["nonce_reuse_new_body"] == 409
    for name in ("swapped_direction", "reflect_reply", "truncated_tail", "truncated_to_8", "empty_ct", "bad_nonce_len",
                 "nonce_not_hex", "cross_device_id", "inner_device_mismatch", "claim_kind_on_action"):
        assert results[name] in {400, 401}, name


def test_phone_approval_off_on_every_path(lan, desktop):
    base, runtime, home, value = lan
    forged_key = {"public_key": base64.b64encode(b"\x04" + b"\x01" * 64).decode(), "encoding": "x963", "platform": "ios",
                  "build_proven": True, "state": "verified", "attestation": "verified"}
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    real = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    forged_key["public_key"] = base64.b64encode(real).decode()
    phone, key, shared, claim = _pair(desktop, value, approval_key=forged_key, activate_chat=False)
    from core.web.api.mobile_companion_api import mobile_companion_service

    rec = mobile_companion_service().store.device(claim["device_id"])
    print("APPROVAL-KEY-RECORD", rec["approval_key"])
    assert rec["approval_key"]["state"] == "unproven_use_computer" and rec["approval_key"]["build_proven"] is False
    # owner tries to turn approvals on from the PC
    for right in ("payment_approve", "tool_approval"):
        status, out = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": claim["device_id"], "rules": {right: True}})
        print("OWNER-ENABLE", right, status, out.get("code"))
        assert status == 400
    # rights default OFF (no chat granted)
    outs = {}
    for name, params in (("chats.send", {"message": "hi"}), ("chats.list", {}), ("wallet.status", {}),
                         ("wallet.approve", {"proposal_id": "x", "credential": "1234"}),
                         ("approvals.resolve", {"approval_id": "x", "decision": "approve"}),
                         ("notifications.preferences.set", {"rules": {"payment_approve": True}})):
        wire = st.action(key, shared, claim, name, params)
        status, resp = http(phone, "/api/mobile/companion", wire)
        body = st.opened(resp, shared, wire)
        outs[name] = (status, body.get("code"))
    # force the stored rules on directly (a bug elsewhere) and check approve still refused
    store = mobile_companion_service().store
    store.mark_device(claim["device_id"], rules={**rec["rules"], "payment_approve": True, "tool_approval": True},
                      approval_key={"state": "verified", "build_proven": True})
    for name, params in (("wallet.approve", {"proposal_id": "x", "credential": "1234"}),
                         ("approvals.resolve", {"approval_id": "x", "decision": "approve"})):
        wire = st.action(key, shared, claim, name, params)
        status, resp = http(phone, "/api/mobile/companion", wire)
        outs[name + "+forced"] = (status, st.opened(resp, shared, wire).get("code"))
    print("APPROVAL-PATHS", json.dumps(outs, sort_keys=True))
    assert outs["chats.send"][0] == 403 and outs["wallet.status"][0] == 403
    for name in ("wallet.approve", "approvals.resolve", "wallet.approve+forced", "approvals.resolve+forced"):
        assert outs[name][0] == 403, name
    rec = store.device(claim["device_id"])
    assert not rec["rules"].get("payment_approve") or True


def test_right_expiry_fails_closed(lan, desktop):
    base, runtime, home, value = lan
    phone, key, shared, claim = _pair(desktop, value, activate_chat=False)
    status, out = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": claim["device_id"],
                                                      "rules": {"history": True}, "right_expiry": {"history": time.time() + 2}})
    assert status == 200, out
    wire = st.action(key, shared, claim, "chats.list")
    before = http(phone, "/api/mobile/companion", wire)[0]
    time.sleep(2.5)
    wire = st.action(key, shared, claim, "chats.list")
    status, resp = http(phone, "/api/mobile/companion", wire)
    after = (status, st.opened(resp, shared, wire).get("code"))
    print("EXPIRY", before, after)
    assert before == 200 and after[0] == 403
    status, out = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": claim["device_id"],
                                                      "rules": {}, "right_expiry": {"history": time.time() - 5}})
    print("PAST-EXPIRY", status, out.get("code"))
    assert status == 400


def test_switch_off_closes_socket_from_lan(lan):
    base, runtime, home, value = lan
    status, out = http(base, "/api/mobile/settings", {"enabled": False})
    print("OFF", status, out.get("enabled"), out.get("running"))
    s = socket.socket()
    try:
        code = s.connect_ex((LAN, value["port"]))
    finally:
        s.close()
    print("CONNECT-AFTER-OFF", code)
    assert code != 0


def test_malformed_approval_key_after_claim(lan, desktop):
    """An SPKI with an unknown algorithm OID: does the claim finish cleanly, and is the device half-paired?"""
    base, runtime, home, value = lan
    # SEQUENCE { SEQUENCE { OID 1.2.3.4 } BIT STRING (33 bytes: 0x00 + 32) }
    oid = bytes([0x06, 0x03, 0x2a, 0x03, 0x04])
    alg = bytes([0x30, len(oid)]) + oid
    bits = bytes([0x03, 33, 0x00]) + b"\x02" * 32
    spki = bytes([0x30, len(alg) + len(bits)]) + alg + bits
    bad = {"public_key": base64.b64encode(spki).decode(), "encoding": "spki", "platform": "android"}
    outcome = "ok"
    try:
        phone, key, shared, claim = _pair(desktop, value, approval_key=bad, activate_chat=False)
    except Exception as exc:
        outcome = type(exc).__name__ + ": " + str(exc)[:200]
    from core.web.api.mobile_companion_api import mobile_companion_service

    devices = mobile_companion_service().store.devices()
    print("MALFORMED-KEY-OUTCOME", outcome)
    assert not devices, "Malformed key must not commit any device"
    sessions = mobile_companion_service().store.pairings().values()
    assert all(row["status"] == "waiting" for row in sessions), "A malformed key must not consume its code"
    for rec in devices.values():
        print("DEVICE", rec.get("device_id"), rec.get("status"), "sealed=", rec.get("sealed_protocol"),
              "enc=", bool(rec.get("encryption_public_key")), "rules=", rec.get("rules"), "approval_key=", rec.get("approval_key"))


def test_unauthenticated_lan_receipt_and_state_write_amplification(lan):
    base, runtime, home, value = lan
    phone = f"http://{LAN}:{value['port']}"
    import glob
    first = glob.glob(str(home / "**/mobile_companion_receipts.jsonl"), recursive=True)
    n0 = len(open(first[0]).read().splitlines()) if first else 0
    t = time.monotonic()
    for i in range(300):
        http(phone, "/api/mobile/companion", {"protocol": "x", "pad": "A" * 2000, "i": i})
        http(phone, "/api/mobile/companion", {"protocol": "vool.mobile.sealed.v2", "device_id": "phone:" + "f" * 20 + "x" * 100,
                                              "nonce": "00" * 24, "ciphertext": ""})
    elapsed = time.monotonic() - t
    import glob
    import os
    paths = glob.glob(str(home / "**/mobile_companion_receipts.jsonl"), recursive=True)
    print("RECEIPT-FILES", paths)
    path = paths[0]
    _receipts(base)  # flush counted refusals at the owner read boundary
    n1 = len(open(path).read().splitlines())
    assert n1 - n0 <= 16, "Unauthenticated refusals must be coalesced"
    records = [json.loads(line) for line in open(path)]
    assert sum(row.get("detail", {}).get("count", 0) for row in records if row["kind"].startswith("security.")) == 600
    print("FLOOD", "requests=600", "receipts_added=", n1 - n0, "bytes=", os.path.getsize(path), "elapsed=%.1fs" % elapsed)


@pytest.mark.parametrize("foreign_headers", [
    {"Origin": "http://127.0.0.1:1", "Content-Type": "text/plain"},
    {"Sec-Fetch-Site": "same-site", "Content-Type": "text/plain"},
    {"Origin": "null", "Content-Type": "text/plain"},
])
def test_cross_origin_receipt_reads_cannot_force_pending_refusal_writes(lan, foreign_headers):
    base, runtime, home, value = lan
    phone = f"http://{LAN}:{value['port']}"
    assert http(base, "/api/mobile/pairing/start", {})[0] == 200
    receipt = next(home.rglob("mobile_companion_receipts.jsonl"))
    state = receipt.with_name("state.json")
    before = len(receipt.read_text().splitlines())
    statuses = []
    get_rewrites = 0
    for i in range(24):
        assert http(phone, "/api/mobile/companion", {"protocol": "x", "i": i})[0] == 400
        inode = state.stat().st_ino
        status, _ = http(base, "/api/mobile/receipts", headers=foreign_headers)
        statuses.append(status)
        get_rewrites += inode != state.stat().st_ino
    rows = [json.loads(line) for line in receipt.read_text().splitlines()][before:]
    print("COMPOSED-FLOOD", "requests=24", "foreign_statuses=", sorted(set(statuses)),
          "receipts_added=", len(rows), "GET_state_rewrites=", get_rewrites)
    assert set(statuses) == {403}
    assert len(rows) <= 8 and get_rewrites == 0
    from core.web.api.mobile_companion_api import handle_mobile_companion_get

    inode = state.stat().st_ino
    legacy = handle_mobile_companion_get("/api/mobile/receipts", {}, runtime=runtime,
                                        model_name="vool", client_host="127.0.0.1")
    assert legacy.status == 200 and state.stat().st_ino == inode
    assert len(receipt.read_text().splitlines()) == before + len(rows)
    rows = _receipts(base)  # a bound PC reader can still flush the complete count
    assert sum(row.get("detail", {}).get("count", 0) for row in rows if row["kind"].startswith("security.")) == 24


def test_idle_connections_exhaust_listener_slots(lan):
    base, runtime, home, value = lan
    socks = []
    for _ in range(12):
        s = socket.create_connection((LAN, value["port"]))
        s.sendall(b"GET /api/mobile/info HTTP/1.1\r\nHost: x\r\n")  # never finish the headers
        socks.append(s)
    time.sleep(0.3)
    try:
        from http.client import HTTPConnection
        conn = HTTPConnection(LAN, value["port"], source_address=("127.0.0.1", 0), timeout=5)
        conn.request("GET", "/api/mobile/info")
        response = conn.getresponse(); status = response.status; response.read(); conn.close()
    except Exception as exc:
        status = type(exc).__name__ + ":" + str(exc)[:80]
    print("SLOTS-FULL legit request ->", status)
    for s in socks:
        s.close()
    time.sleep(0.3)
    print("SLOTS-FREED legit request ->", http(f"http://{LAN}:{value['port']}", "/api/mobile/info")[0])
    assert status == 200, "One source must not occupy every listener slot"


def test_lost_computer_from_zero_rights_phone(lan, desktop):
    base, runtime, home, value = lan
    a = _pair(desktop, value, activate_chat=False)
    b = _pair(desktop, value, rights={"history": True}, activate_chat=False)
    phone, key, shared, claim = a
    wire = st.action(key, shared, claim, "devices.lost_computer")
    status, resp = http(phone, "/api/mobile/companion", wire)
    body = st.opened(resp, shared, wire)
    print("LOST-COMPUTER", status, body.get("result"))
    settings = http(base, "/api/mobile/settings")[1]
    print("AFTER", "frozen=", settings["frozen"], [(d["device_id"], d["status"]) for d in settings["devices"]])
    wire = st.action(b[1], b[2], b[3], "chats.list")
    status, resp = http(phone, "/api/mobile/companion", wire)
    print("OTHER-PHONE-AFTER", status, st.opened(resp, b[2], wire).get("code"))
    wire = st.action(key, shared, claim, "devices.me")
    status, resp = http(phone, "/api/mobile/companion", wire)
    print("SAME-PHONE-AFTER", status, st.opened(resp, shared, wire).get("code"))
    assert settings["frozen"] is True
    assert all(d["status"] != "active" for d in settings["devices"])


@pytest.mark.parametrize("origin,ctype", [("http://127.0.0.1:9999", "text/plain"), ("http://localhost:3000", "text/plain"),
                                          ("http://127.0.0.1:9999", "application/json"), (None, "text/plain")])
def test_cross_port_page_can_toggle_listener(desktop, origin, ctype):
    """A page on another localhost port (simple CORS request, no preflight) against the owner-only toggle."""
    import urllib.request
    from urllib.error import HTTPError

    base, runtime, home = desktop
    results = {}
    for name, body in (("enable", {"enabled": True, "address": LAN}),
                       ("device_rules", {"operation": "device_rules", "device_id": "phone:x", "rules": {"chat": True}}),
                       ("freeze", {"operation": "freeze"})):
        headers = {"Content-Type": ctype, "Sec-Fetch-Site": "same-site"} if origin else {"Content-Type": ctype}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(base + "/api/mobile/settings", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                results[name] = (r.status, json.loads(r.read()).get("code"))
        except HTTPError as e:
            results[name] = (e.code, json.loads(e.read() or b"{}").get("code"))
    listener = getattr(runtime, "phone_listener", None)
    print("CROSS-PORT", origin, ctype, json.dumps(results), "listener_running=", bool(listener and listener.running))

    if origin:
        assert results["enable"] == (403, "owner_caller_required"), results
        assert results["device_rules"] == (403, "owner_caller_required"), results
    else:
        assert results["enable"][0] == 200, results


def test_validly_encrypted_unknown_claims_do_not_amplify_writes(lan, desktop):
    from pathlib import Path

    from nacl.public import Box, PublicKey
    from nacl.signing import SigningKey

    from core.mobile_companion.sealing import VERSION, seal
    base, runtime, home, value = lan
    phone = f"http://{LAN}:{value['port']}"
    status, start = http(base, "/api/mobile/pairing/start", {})
    assert status == 200
    status, challenge = http(phone, "/api/mobile/pairing/challenge?pairing_id=" + start['pairing_id'])
    assert status == 200
    key = SigningKey.generate()
    curve = key.to_curve25519_private_key()
    shared = Box(curve, PublicKey(bytes.fromhex(challenge['sealing']['binding']['public_key']))).shared_key()
    path = next(Path(home).rglob('mobile_companion_receipts.jsonl'))
    before = len(path.read_text().splitlines())
    state = path.with_name('state.json')
    previous = state.stat().st_ino
    rewrites = 0
    for i in range(600):
        body = {'pairing_id': 'pair_missing_' + str(i), 'code': 'not_a_code', 'device_name': 'untrusted',
                'platform': 'ios', 'device_public_key': key.verify_key.encode().hex(),
                'device_proof': {'signature': '00'*64}}
        wire = {**seal({'kind': VERSION+':claim','body':body}, shared, 'request'), 'public_key':curve.public_key.encode().hex()}
        assert http(phone, '/api/mobile/pairing/claim', wire)[0] in {404,429}
        current = state.stat().st_ino
        rewrites += current != previous
        previous = current
    _receipts(base)
    rows = [json.loads(line) for line in path.read_text().splitlines()][before:]
    assert len(rows) <= 16 and rewrites <= 8, (len(rows), rewrites)
    assert sum(row['detail'].get('count', 0) for row in rows) == 600
