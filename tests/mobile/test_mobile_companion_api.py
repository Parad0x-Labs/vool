"""Mobile companion API — unit + sabotage tests.

Covers the full pairing/auth/revocation surface of
core/web/api/mobile_companion_api.py, the service registration seam in
core/web/api/service.py, and the journey-level actions (chats, attachments,
approvals, status, notifications) against the REAL underlying handlers.
Sabotage rows prove the three named threats — expired, replayed, revoked —
plus tampered HMACs, clock-window replays, rate limits, the relay gate and
restart rehydration.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from nacl.signing import SigningKey

from core.device_link import identity as device_identity
from core.mode_permission_policy import (
    decide_tool_call,
    reset_mode_permission_state,
    set_active_mode,
)
from core.runtime_paths import configure_runtime_home
from core.web.api import mobile_companion_api as mca
from core.web.api.mobile_companion_api import (
    MobileCompanionService,
    reset_mobile_companion_service,
)
from core.web.api.runtime import RuntimeServices

PHONE_HOST = "192.168.1.55"

import itertools


def _tiny_png() -> bytes:
    """A structurally valid 1x1 PNG — the staging door checks chunk CRCs, so a
    hand-waved 'PNG-like' fixture is (correctly) refused."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))

DESKTOP_HOST = "127.0.0.1"


@pytest.fixture(autouse=True)
def _mobile_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    configure_runtime_home(tmp_path)
    reset_mobile_companion_service()
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()
    reset_mobile_companion_service()


class _StubAgent:
    """Minimal model lane: the /api/chat pipeline needs a non-empty answer
    (zero-byte replies are honestly refused downstream). Persists the turn the
    way the real lane does (core/agent_runtime/chat_surface.py), so the
    transcript readers are exercised for real — same pattern as
    tests/test_chat_attachments_api.py's fake agent."""

    def run_once(self, text, *, session_id_override=None, source_context=None):
        from core.persistent_memory import append_conversation_event

        append_conversation_event(
            session_id=str(session_id_override or ""),
            user_input=text,
            assistant_output="stub reply from the model lane",
            source_context=source_context,
        )
        return {"response": "stub reply from the model lane", "confidence": 0.9}


@pytest.fixture()
def runtime():
    from storage.migrations import run_migrations

    run_migrations()  # the chat ingress ledger (invocation_requests) lives in the store
    rt = RuntimeServices(display_name="VOOL")
    rt.agent = _StubAgent()
    return rt


def _post(path, body, *, runtime, client_host=DESKTOP_HOST, headers=None):
    return mca.handle_mobile_companion_post(
        path, body, headers or {}, runtime=runtime, model_name="vool",
        client_host=client_host, workspace_root_provider=lambda: "/tmp",
    )


def _get(path, query=None, *, runtime, client_host=DESKTOP_HOST):
    return mca.handle_mobile_companion_get(
        path or {}, query or {}, runtime=runtime, model_name="vool", client_host=client_host,
    )


def _body(response) -> dict:
    return json.loads(response.body or b"{}")


_PAIR_COUNTER = itertools.count()
#: signing keys of successfully paired phones, by device_id — the tests ARE
#: the phone: they hold the private halves and sign like the app does.
_PHONE_KEYS: dict[str, SigningKey] = {}


def _new_phone_key() -> SigningKey:
    return SigningKey.generate()


def _public_hex(key: SigningKey) -> str:
    return key.verify_key.encode().hex()


def _pairing_proof(key: SigningKey, *, pairing_id: str, code: str, challenge: str,
                   public_key: str, expires_at_epoch: int) -> str:
    payload = mca._canonical_bytes({
        "kind": "vool.mobile.pairing.proof.v1",
        "pairing_id": pairing_id,
        "code": code,
        "challenge": challenge,
        "device_public_key": public_key,
        "expires_at": int(expires_at_epoch),
    })
    return key.sign(payload).signature.hex()


def _pair(runtime, *, client_host=PHONE_HOST, phone_key: SigningKey | None = None):
    """Owner starts pairing; a phone fetches the challenge and claims with a
    signed proof of possession. Returns (claim response, start response)."""
    key = phone_key or _new_phone_key()
    start = _post("/api/mobile/pairing/start", {"device_hint": "test phone"}, runtime=runtime)
    assert start.status == 200, _body(start)
    start_body = _body(start)
    challenge = _get("/api/mobile/pairing/challenge",
                     {"pairing_id": [start_body["pairing_id"]]}, runtime=runtime, client_host=client_host)
    assert challenge.status == 200, _body(challenge)
    ch = _body(challenge)
    claim = _post("/api/mobile/pairing/claim", {
        "pairing_id": start_body["pairing_id"],
        "code": start_body["code"],
        "device_name": "JUnit Phone",
        "platform": "ios",
        "device_public_key": _public_hex(key),
        "device_proof": {
            "algorithm": "ed25519",
            "signature": _pairing_proof(
                key,
                pairing_id=start_body["pairing_id"],
                code=start_body["code"],
                challenge=ch["challenge"],
                public_key=_public_hex(key),
                expires_at_epoch=ch["expires_at_epoch"],
            ),
        },
    }, runtime=runtime, client_host=client_host)
    if claim.status == 200:
        _PHONE_KEYS[_body(claim)["device_id"]] = key
    return claim, start_body


def _sign(claim: dict, action: str, params: dict, *, nonce=None, timestamp=None, secret=None,
          phone_key: SigningKey | None = None):
    """Build a companion request the way the phone does: grant HMAC + Ed25519
    device signature over the same canonical bytes."""
    from core.device_link.protocol import request_signature_payload

    nonce = nonce or uuid.uuid4().hex
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    payload = request_signature_payload(
        grant_id=claim["grant_id"], verb="vool.action",
        params={"action": action, **params}, nonce=nonce, timestamp=timestamp)
    key = (secret or claim["grant_secret"]).encode("utf-8")
    device_id = claim["device_id"]
    signing_key = phone_key if phone_key is not None else _PHONE_KEYS.get(device_id)
    request_proof = {
        "kind": "vool.mobile.request.v1",
        "device_id": device_id,
        "action": action,
        "params_sha256": hashlib.sha256(mca._canonical_bytes(params)).hexdigest(),
        "nonce": nonce,
        "timestamp": timestamp,
        "grant_id": claim["grant_id"],
    }
    device_signature = (
        signing_key.sign(mca._canonical_bytes(request_proof)).signature.hex()
        if signing_key is not None else "00" * 64
    )
    return {
        "device_id": device_id,
        "grant_id": claim["grant_id"],
        "action": action,
        "params": params,
        "nonce": nonce,
        "timestamp": timestamp,
        "signature": hmac_mod.new(key, mca._canonical_bytes(payload), hashlib.sha256).hexdigest(),
        "device_signature": device_signature,
    }


def _companion(claim, action, params=None, *, runtime, client_host=PHONE_HOST, **sign_kw):
    request = _sign(claim, action, params or {}, **sign_kw)
    return _post("/api/mobile/companion", request, runtime=runtime, client_host=client_host)


# ---------------------------------------------------------------------------
# Pairing lifecycle
# ---------------------------------------------------------------------------

class TestPairing:
    def test_start_is_owner_local_only(self, runtime):
        r = _post("/api/mobile/pairing/start", {}, runtime=runtime, client_host=PHONE_HOST)
        assert r.status == 403 and _body(r)["code"] == "owner_local_required"

    def test_start_returns_short_lived_single_use_code(self, runtime):
        r = _post("/api/mobile/pairing/start", {}, runtime=runtime, headers={"host": "127.0.0.1:11435"})
        body = _body(r)
        assert r.status == 200
        assert len(body["code"]) == 8
        assert body["expires_in_seconds"] == mca.pairing_ttl_seconds()
        assert body["qr_uri"].startswith("vool-pair://127.0.0.1:11435?")
        assert "fp=" + body["desktop_fingerprint"] in body["qr_uri"]
        assert "pid=" + body["pairing_id"] in body["qr_uri"]
        # Receipt recorded.
        rows, _, verified = mca.mobile_companion_service().store.receipts()
        assert verified is True
        assert rows[-1]["kind"] == "pairing.started"

    def test_claim_happy_path_mints_grant_and_signed_proof(self, runtime):
        claim_r, start = _pair(runtime)
        assert claim_r.status == 200, _body(claim_r)
        claim = _body(claim_r)
        assert claim["device_id"].startswith("phone:")
        assert claim["grant_secret"] and claim["grant_id"]
        assert claim["envelope"] == "VIEW_ONLY"
        # Desktop proof verifies under the public key that hashes to the fingerprint.
        service = mca.mobile_companion_service()
        public_hex = device_identity.public_hex(service.authority_key())
        assert hashlib.sha256(bytes.fromhex(public_hex)).hexdigest() == claim["desktop_fingerprint"]
        payload = mca._canonical_bytes(claim["desktop_proof"]["payload"])
        assert device_identity.load_or_create  # module surface
        from nacl.signing import VerifyKey
        VerifyKey(bytes.fromhex(public_hex)).verify(payload, bytes.fromhex(claim["desktop_proof"]["signature"]))
        # Pairing session consumed.
        status = _get("/api/mobile/pairing/status", {"pairing_id": [start["pairing_id"]]}, runtime=runtime)
        assert _body(status)["status"] == "claimed"

    def test_claim_with_bad_code_increments_attempts_then_locks(self, runtime):
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        for _ in range(mca._PAIRING_MAX_ATTEMPTS):
            r = _post("/api/mobile/pairing/claim", {
                "pairing_id": start["pairing_id"], "code": "WRONGWRN",
                "device_name": "x", "platform": "ios", "device_public_key": "ab" * 32,
            "device_proof": {"algorithm": "ed25519", "signature": "11" * 64},
            }, runtime=runtime, client_host=PHONE_HOST)
            assert r.status == 401 and _body(r)["code"] == "bad_code"
        locked = _post("/api/mobile/pairing/claim", {
            "pairing_id": start["pairing_id"], "code": start["code"],
            "device_name": "x", "platform": "ios", "device_public_key": "ab" * 32,
            "device_proof": {"algorithm": "ed25519", "signature": "11" * 64},
        }, runtime=runtime, client_host=PHONE_HOST)
        assert locked.status == 429 and _body(locked)["code"] == "pairing_locked"

    def test_sabotage_expired_code_is_refused_with_receipt(self, runtime, monkeypatch):
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        real_now = mca._now()
        monkeypatch.setattr(mca, "_now", lambda: real_now + start["expires_in_seconds"] + 1)
        r = _post("/api/mobile/pairing/claim", {
            "pairing_id": start["pairing_id"], "code": start["code"],
            "device_name": "x", "platform": "ios", "device_public_key": "ab" * 32,
            "device_proof": {"algorithm": "ed25519", "signature": "11" * 64},
        }, runtime=runtime, client_host=PHONE_HOST)
        assert r.status == 410 and _body(r)["code"] == "pairing_expired"
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        assert rows[-1]["kind"] == "pairing.failed" and rows[-1]["detail"]["reason"] == "expired"
        # And the pairing status shows expired to the desktop.
        status = _get("/api/mobile/pairing/status", {"pairing_id": [start["pairing_id"]]}, runtime=runtime)
        assert _body(status)["status"] == "expired"

    def test_sabotage_replayed_code_is_single_use(self, runtime):
        claim_r, start = _pair(runtime)
        assert claim_r.status == 200
        again = _post("/api/mobile/pairing/claim", {
            "pairing_id": start["pairing_id"], "code": start["code"],
            "device_name": "evil twin", "platform": "ios", "device_public_key": "cd" * 32,
            "device_proof": {"algorithm": "ed25519", "signature": "11" * 64},
        }, runtime=runtime, client_host=PHONE_HOST)
        assert again.status == 409 and _body(again)["code"] == "pairing_used"
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        assert rows[-1]["detail"]["reason"] == "code_replayed"

    def test_claim_rate_limited_per_host(self, runtime, monkeypatch):
        for _ in range(mca._PAIR_START_RATE_CAP):
            assert _post("/api/mobile/pairing/start", {}, runtime=runtime).status == 200
        limited = _post("/api/mobile/pairing/start", {}, runtime=runtime)
        assert limited.status == 429 and _body(limited)["code"] == "pairing_rate_limited"

    def test_device_limit_enforced(self, runtime, monkeypatch):
        monkeypatch.setenv("VOOL_MOBILE_MAX_DEVICES", "1")
        first, _ = _pair(runtime)
        assert first.status == 200
        start2 = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        second = _post("/api/mobile/pairing/claim", {
            "pairing_id": start2["pairing_id"], "code": start2["code"],
            "device_name": "second", "platform": "android", "device_public_key": "ef" * 32,
            "device_proof": {"algorithm": "ed25519", "signature": "11" * 64},
        }, runtime=runtime, client_host=PHONE_HOST)
        assert second.status == 409 and _body(second)["code"] == "device_limit"


# ---------------------------------------------------------------------------
# Proof of possession (amendment: the phone's key is load-bearing)
# ---------------------------------------------------------------------------

class TestProofOfPossession:
    def test_claim_without_proof_is_refused(self, runtime):
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        r = _post("/api/mobile/pairing/claim", {
            "pairing_id": start["pairing_id"], "code": start["code"],
            "device_name": "x", "platform": "ios", "device_public_key": _public_hex(_new_phone_key()),
        }, runtime=runtime, client_host=PHONE_HOST)
        assert r.status == 400 and _body(r)["code"] == "invalid_request"

    def test_sabotage_forged_public_key_fails_claim(self, runtime):
        # The claimant registers victim_pub but signs with its OWN key: the
        # proof cannot verify, so no grant is ever issued.
        victim_key = _new_phone_key()
        attacker_key = _new_phone_key()
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        ch = _body(_get("/api/mobile/pairing/challenge", {"pairing_id": [start["pairing_id"]]}, runtime=runtime))
        forged = _post("/api/mobile/pairing/claim", {
            "pairing_id": start["pairing_id"], "code": start["code"],
            "device_name": "attacker", "platform": "ios",
            "device_public_key": _public_hex(victim_key),
            "device_proof": {"algorithm": "ed25519", "signature": _pairing_proof(
                attacker_key, pairing_id=start["pairing_id"], code=start["code"],
                challenge=ch["challenge"], public_key=_public_hex(victim_key),
                expires_at_epoch=ch["expires_at_epoch"])},
        }, runtime=runtime, client_host=PHONE_HOST)
        assert forged.status == 401 and _body(forged)["code"] == "proof_of_possession_failed"
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        assert rows[-1]["kind"] == "pairing.failed" and rows[-1]["detail"]["reason"] == "proof_of_possession"
        # And nothing registered under the victim's device_id.
        assert mca.mobile_companion_service().store.device(
            "phone:" + hashlib.sha256(bytes.fromhex(_public_hex(victim_key))).hexdigest()[:20]) is None

    def test_sabotage_stolen_grant_without_device_key_fails(self, runtime):
        # Attacker holds the grant secret (correct HMAC) but NOT the phone's
        # private key: the per-request Ed25519 proof fails.
        claim = _body(_pair(runtime)[0])
        r = _companion(claim, "devices.me", runtime=runtime, phone_key=_new_phone_key())
        assert r.status == 401 and _body(r)["code"] == "device_signature_invalid"
        missing = _post("/api/mobile/companion", {k: v for k, v in _sign(claim, "devices.me", {}).items() if k != "device_signature"},
                        runtime=runtime, client_host=PHONE_HOST)
        assert missing.status == 400 and _body(missing)["code"] == "invalid_request"
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        assert any(row["kind"] == "security.device_signature_invalid" for row in rows)

    def test_sabotage_stolen_device_key_without_live_grant_fails(self, runtime):
        # Attacker holds the phone's private key, but the device was revoked:
        # revocation kills BOTH factors.
        claim = _body(_pair(runtime)[0])
        assert _post("/api/mobile/devices/revoke", {"device_id": claim["device_id"]}, runtime=runtime).status == 200
        r = _companion(claim, "devices.me", runtime=runtime)  # correctly signed by the real key
        assert r.status == 401 and _body(r)["code"] == "device_revoked"
        # And a forged grant_id under a stolen key buys nothing.
        tampered = dict(_sign(claim, "devices.me", {}))
        tampered["grant_id"] = str(uuid.uuid4())
        r2 = _post("/api/mobile/companion", tampered, runtime=runtime, client_host=PHONE_HOST)
        assert r2.status == 401

    def test_key_loss_forces_repairing(self, runtime):
        # The phone loses its seed and generates a NEW identity: the new key
        # cannot ride the old grant (different device_id), and the old
        # device_id rejects the new key's signatures.
        old_claim = _body(_pair(runtime)[0])
        new_key = _new_phone_key()
        # New key signing for the OLD device id -> device signature invalid.
        r = _companion(old_claim, "devices.me", runtime=runtime, phone_key=new_key)
        assert r.status == 401 and _body(r)["code"] == "device_signature_invalid"
        # The honest path is a fresh pairing session (new code, new proof).
        new_claim = _body(_pair(runtime, phone_key=new_key)[0])
        assert new_claim["device_id"] != old_claim["device_id"]
        ok = _companion(new_claim, "devices.me", runtime=runtime)
        assert ok.status == 200

    def test_restart_preserves_identity(self, runtime):
        # Same seed -> same device_id; after a service restart over the same
        # store the phone keeps its identity and its grant still answers.
        key = _new_phone_key()
        claim = _body(_pair(runtime, phone_key=key)[0])
        reset_mobile_companion_service()
        r = _companion(claim, "devices.me", runtime=runtime)
        assert r.status == 200
        assert _body(r)["result"]["device_id"] == claim["device_id"]
        # The stored credential blob carries the seed it was paired with.
        rec = mca.mobile_companion_service().store.device(claim["device_id"])
        assert rec["public_key"] == _public_hex(key)

    def test_replay_and_clock_attacks_still_refused(self, runtime):
        claim = _body(_pair(runtime)[0])
        request = _sign(claim, "devices.me", {}, nonce="fixed-nonce-amend")
        first = _post("/api/mobile/companion", request, runtime=runtime, client_host=PHONE_HOST)
        assert first.status == 200
        replay = _post("/api/mobile/companion", request, runtime=runtime, client_host=PHONE_HOST)
        assert replay.status == 409 and _body(replay)["code"] == "replay_detected"
        stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        r = _companion(claim, "devices.me", runtime=runtime, timestamp=stale)
        assert r.status == 401 and _body(r)["code"] == "timestamp_window"


class TestPairingSurface:
    def test_challenge_endpoint_shape(self, runtime):
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        r = _get("/api/mobile/pairing/challenge", {"pairing_id": [start["pairing_id"]]}, runtime=runtime, client_host=PHONE_HOST)
        body = _body(r)
        assert r.status == 200
        assert len(body["challenge"]) == 64
        assert body["expires_at_epoch"] == start["expires_at_epoch"]
        assert body["desktop_fingerprint"] == start["desktop_fingerprint"]
        assert len(body["desktop_public_key"]) == 64
        assert _get("/api/mobile/pairing/challenge", {"pairing_id": ["pair_nope"]}, runtime=runtime).status == 404

    def test_qr_uri_pins_url_id_code_expiry_fingerprint(self, runtime):
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime, headers={"host": "192.168.1.9:11435"}))
        uri = start["qr_uri"]
        assert uri.startswith("vool-pair://192.168.1.9:11435?")
        assert "fp=" + start["desktop_fingerprint"] in uri
        assert "pid=" + start["pairing_id"] in uri
        assert "code=" + start["code"] in uri
        assert f"exp={start['expires_at_epoch']}" in uri

    def test_pairing_page_renders_real_qr_surface(self, runtime):
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        r = _get("/api/mobile/pairing/page", {"pairing_id": [start["pairing_id"]]}, runtime=runtime)
        assert r.status == 200
        assert r.content_type.startswith("text/html")
        html = r.body.decode("utf-8")
        # The vendored QR renderer is inlined (no CDN, no client guessing).
        assert "qrcode-generator" in html or "QRCode" in html or "qrcode" in html
        # The pinned payload fields are embedded for the client-side render.
        assert start["code"] in html
        assert start["desktop_fingerprint"] in html
        assert f"exp={start['expires_at_epoch']}" in html or str(start["expires_at_epoch"]) in html
        # Owner-local only.
        assert _get("/api/mobile/pairing/page", {"pairing_id": [start["pairing_id"]]},
                    runtime=runtime, client_host=PHONE_HOST).status == 403

    def test_pairing_page_unknown_session(self, runtime):
        assert _get("/api/mobile/pairing/page", {"pairing_id": ["pair_nope"]}, runtime=runtime).status == 404


# ---------------------------------------------------------------------------
# Authentication surface
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_happy_path_devices_me(self, runtime):
        claim = _body(_pair(runtime)[0])
        r = _companion(claim, "devices.me", runtime=runtime)
        assert r.status == 200
        result = _body(r)["result"]
        assert result["device_id"] == claim["device_id"]
        assert result["status"] == "active"

    def test_missing_fields_refused(self, runtime):
        claim = _body(_pair(runtime)[0])
        r = _post("/api/mobile/companion", {"device_id": claim["device_id"]}, runtime=runtime, client_host=PHONE_HOST)
        assert r.status == 400 and _body(r)["code"] == "invalid_request"

    def test_unknown_device_refused(self, runtime):
        claim = _body(_pair(runtime)[0])
        request = _sign(claim, "devices.me", {})
        request["device_id"] = "phone:doesnotexist"
        r = _post("/api/mobile/companion", request, runtime=runtime, client_host=PHONE_HOST)
        assert r.status == 401 and _body(r)["code"] == "device_unknown"

    def test_tampered_hmac_refused(self, runtime):
        claim = _body(_pair(runtime)[0])
        request = _sign(claim, "devices.me", {})
        request["signature"] = ("0" if request["signature"][0] != "0" else "1") + request["signature"][1:]
        r = _post("/api/mobile/companion", request, runtime=runtime, client_host=PHONE_HOST)
        assert r.status == 401 and _body(r)["code"] == "signature_invalid"

    def test_wrong_secret_refused(self, runtime):
        claim = _body(_pair(runtime)[0])
        r = _companion(claim, "devices.me", runtime=runtime, secret="not-the-secret")
        assert r.status == 401 and _body(r)["code"] == "signature_invalid"

    def test_sabotage_replayed_nonce_refused_with_receipt(self, runtime):
        claim = _body(_pair(runtime)[0])
        request = _sign(claim, "devices.me", {}, nonce="fixed-nonce-1")
        first = _post("/api/mobile/companion", request, runtime=runtime, client_host=PHONE_HOST)
        assert first.status == 200
        replay = _post("/api/mobile/companion", request, runtime=runtime, client_host=PHONE_HOST)
        assert replay.status == 409 and _body(replay)["code"] == "replay_detected"
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        assert rows[-1]["kind"] == "security.replay_detected"

    def test_stale_timestamp_refused(self, runtime):
        claim = _body(_pair(runtime)[0])
        stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        r = _companion(claim, "devices.me", runtime=runtime, timestamp=stale)
        assert r.status == 401 and _body(r)["code"] == "timestamp_window"

    def test_sabotage_revoked_device_loses_authority_immediately(self, runtime):
        claim = _body(_pair(runtime)[0])
        ok = _companion(claim, "devices.me", runtime=runtime)
        assert ok.status == 200
        revoke = _post("/api/mobile/devices/revoke", {"device_id": claim["device_id"], "reason": "lost phone"}, runtime=runtime)
        assert revoke.status == 200
        after = _companion(claim, "devices.me", runtime=runtime)
        assert after.status == 401 and _body(after)["code"] == "device_revoked"
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        kinds = [row["kind"] for row in rows]
        assert "device.revoked" in kinds and "security.revoked_device_attempt" in kinds

    def test_revocation_survives_service_restart(self, runtime):
        claim = _body(_pair(runtime)[0])
        assert _post("/api/mobile/devices/revoke", {"device_id": claim["device_id"]}, runtime=runtime).status == 200
        # Simulate a daemon restart: fresh singleton over the same VOOL_HOME.
        reset_mobile_companion_service()
        after = _companion(claim, "devices.me", runtime=runtime)
        assert after.status == 401 and _body(after)["code"] == "device_revoked"

    def test_live_grant_survives_service_restart(self, runtime):
        claim = _body(_pair(runtime)[0])
        reset_mobile_companion_service()
        after = _companion(claim, "devices.me", runtime=runtime)
        assert after.status == 200

    def test_action_catalog_is_closed(self, runtime):
        claim = _body(_pair(runtime)[0])
        for action, params in [
            ("fs.read", {"path": "/etc/passwd"}),
            ("term.run", {"command": "rm -rf /"}),
            ("fs.write", {"path": "/tmp/x", "content": "x"}),
        ]:
            r = _companion(claim, action, params, runtime=runtime)
            assert r.status == 403 and _body(r)["code"] == "action_not_offered", action
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        assert any(row["kind"] == "security.action_refused" for row in rows)

    def test_device_rate_limit(self, runtime):
        claim = _body(_pair(runtime)[0])
        codes = set()
        for _ in range(mca._DEVICE_RATE_CAP + 2):
            r = _companion(claim, "devices.me", runtime=runtime)
            codes.add(r.status)
            if r.status == 429:
                assert _body(r)["code"] == "device_rate_limited"
                return
        pytest.fail("device rate limit never fired")


# ---------------------------------------------------------------------------
# Relay gate
# ---------------------------------------------------------------------------

class TestRelayGate:
    def test_relay_traffic_refused_while_disabled(self, runtime):
        start = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))
        r = _post("/api/mobile/pairing/claim", {
            "pairing_id": start["pairing_id"], "code": start["code"],
            "device_name": "x", "platform": "ios", "device_public_key": "ab" * 32,
            "device_proof": {"algorithm": "ed25519", "signature": "11" * 64},
        }, runtime=runtime, client_host=PHONE_HOST, headers={"x-vool-mobile-relay": "1"})
        assert r.status == 403 and _body(r)["code"] == "relay_disabled"
        rows, _, _ = mca.mobile_companion_service().store.receipts()
        assert rows[-1]["kind"] == "security.relay_refused"

    def test_relay_disabled_by_default_and_local_mode_advertised(self, runtime):
        claim = _body(_pair(runtime)[0])
        r = _companion(claim, "status.snapshot", runtime=runtime)
        assert _body(r)["result"]["relay"] == {"enabled": False, "mode": "local-network"}

    def test_explicit_config_enables_relay_flag_only(self, runtime, monkeypatch):
        monkeypatch.setenv("VOOL_MOBILE_RELAY_ENABLED", "1")
        assert mca.relay_enabled() is True
        r = _companion(_body(_pair(runtime)[0]), "status.snapshot", runtime=runtime)
        assert _body(r)["result"]["relay"]["enabled"] is True


# ---------------------------------------------------------------------------
# Journey actions against the REAL underlying handlers
# ---------------------------------------------------------------------------

class TestJourneyActions:
    def test_status_snapshot_shape(self, runtime):
        claim = _body(_pair(runtime)[0])
        r = _companion(claim, "status.snapshot", runtime=runtime)
        result = _body(r)["result"]
        assert result["desktop"]["display_name"] == "VOOL"
        assert result["desktop"]["fingerprint"] == claim["desktop_fingerprint"]
        assert result["model"]["current"] == "vool"
        assert "cloud_state" in result["model"]

    def test_chats_prepare_and_list(self, runtime):
        claim = _body(_pair(runtime)[0])
        prep = _companion(claim, "chats.prepare", runtime=runtime)
        result = _body(prep)["result"]
        assert prep.status == 200
        assert result["chat_id"].startswith("mob-")
        assert result["canonical_chat_id"].startswith("openclaw:")
        listing = _companion(claim, "chats.list", runtime=runtime)
        assert listing.status == 200 and "chats" in _body(listing)["result"]

    def test_attachment_upload_stages_through_real_door(self, runtime):
        claim = _body(_pair(runtime)[0])
        chat_id = _body(_companion(claim, "chats.prepare", runtime=runtime))["result"]["chat_id"]
        photo = _tiny_png()
        import base64

        r = _companion(claim, "attachments.upload", {
            "chat_id": chat_id,
            "filename": "photo.png",
            "content_b64": base64.b64encode(photo).decode(),
            "media_type": "image/png",
            "kind": "photo",
        }, runtime=runtime)
        assert r.status == 201, _body(r)
        record = _body(r)["result"]["attachment"]
        assert record["sha256"] == hashlib.sha256(photo).hexdigest()
        assert "path" not in json.dumps(record)  # never a filesystem path

    def test_attachment_upload_ceiling(self, runtime):
        claim = _body(_pair(runtime)[0])
        chat_id = _body(_companion(claim, "chats.prepare", runtime=runtime))["result"]["chat_id"]
        import base64

        r = _companion(claim, "attachments.upload", {
            "chat_id": chat_id, "filename": "big.bin",
            "content_b64": base64.b64encode(b"\x00" * (mca._MOBILE_UPLOAD_CEILING + 1)).decode(),
        }, runtime=runtime)
        assert r.status == 413

    def test_chats_send_rides_the_real_chat_pipeline(self, runtime):
        claim = _body(_pair(runtime)[0])
        chat_id = _body(_companion(claim, "chats.prepare", runtime=runtime))["result"]["chat_id"]
        r = _companion(claim, "chats.send", {"chat_id": chat_id, "message": "hello from the phone"}, runtime=runtime)
        assert r.status == 200, _body(r)
        result = _body(r)["result"]
        # Phone-facing continuity: the phone's handle survives the turn.
        assert result["chat_id"] == chat_id
        assert result["canonical_chat_id"].startswith("openclaw:")
        assert isinstance(result["reply"], str)
        history = _companion(claim, "chats.history", {"chat_id": chat_id}, runtime=runtime)
        assert history.status == 200
        messages = _body(history)["result"]["messages"]
        assert any(m.get("role") == "user" and "hello from the phone" in str(m.get("content")) for m in messages)

    def test_approvals_pending_and_resolve(self, runtime, tmp_path):
        # Mint a REAL pending approval through the product's own permission gate.
        session = "chat-mobile-unit"
        set_active_mode(session, "manual", project_id="", client_turn_id="turn-unit")
        decision = decide_tool_call(
            intent="workspace.write_file",
            arguments={"path": "notes/unit.txt", "content": "x"},
            task_id="turn-unit",
            source_context={"runtime_session_id": session, "operating_mode": "manual",
                            "workspace_root": str(tmp_path)},
        )
        assert decision.approval_request is not None
        token = decision.approval_request["approval_id"]

        claim = _body(_pair(runtime)[0])
        pending = _companion(claim, "approvals.pending", runtime=runtime)
        assert pending.status == 200
        result = _body(pending)["result"]
        assert result["needs_approval"] is True
        row = next(a for a in result["approvals"] if a["approval_id"] == token)
        assert row["intent"] == "workspace.write_file"

        resolved = _companion(claim, "approvals.resolve", {
            "approval_id": token, "decision": "allow", "scope": "once",
        }, runtime=runtime)
        assert resolved.status == 200
        assert _body(resolved)["result"]["approval"]["status"] == "approved"
        # Consumed: a second resolve is a typed conflict.
        double = _companion(claim, "approvals.resolve", {
            "approval_id": token, "decision": "deny",
        }, runtime=runtime)
        assert double.status == 409

        # The REFUSE leg: a second real pending approval, denied from the phone.
        denied_decision = decide_tool_call(
            intent="workspace.write_file",
            arguments={"path": "notes/refused.txt", "content": "y"},
            task_id="turn-unit-2",
            source_context={"runtime_session_id": session, "operating_mode": "manual",
                            "workspace_root": str(tmp_path)},
        )
        denied_token = denied_decision.approval_request["approval_id"]
        refused = _companion(claim, "approvals.resolve", {
            "approval_id": denied_token, "decision": "deny",
        }, runtime=runtime)
        assert refused.status == 200
        assert _body(refused)["result"]["approval"]["status"] == "denied"
        # A denial never widens scope.
        assert _body(refused)["result"]["approval"]["scope"] == "once"
        still_gone = _companion(claim, "approvals.pending", runtime=runtime)
        assert not any(a["approval_id"] == denied_token
                       for a in _body(still_gone)["result"]["approvals"])

    def test_notifications_poll_classifies_completion_and_failure(self, runtime):
        from core.runtime_task_events import emit_runtime_event

        canonical = "openclaw:" + "a1" * 10
        emit_runtime_event({"runtime_session_id": canonical}, event_type="task_finalizing", message="done")
        emit_runtime_event({"runtime_session_id": canonical}, event_type="tool_failed", message="boom")
        claim = _body(_pair(runtime)[0])
        r = _companion(claim, "notifications.poll", {"session": canonical}, runtime=runtime)
        events = _body(r)["result"]["events"]
        kinds = {e["event_type"]: e["mobile_kind"] for e in events}
        assert kinds.get("task_finalizing") == "completion"
        assert kinds.get("tool_failed") == "failure"

    def test_proof_get_requires_binding(self, runtime):
        claim = _body(_pair(runtime)[0])
        r = _companion(claim, "proof.get", {"chat_id": "openclaw:nope", "request_id": "req-nope"}, runtime=runtime)
        assert r.status == 404 and _body(r)["result"]["error"] == "proof_not_bound"
        missing = _companion(claim, "proof.get", {}, runtime=runtime)
        assert missing.status == 400


# ---------------------------------------------------------------------------
# Desktop management + receipts + seam
# ---------------------------------------------------------------------------

class TestDesktopManagement:
    def test_device_listing_never_leaks_the_grant_secret(self, runtime):
        claim = _body(_pair(runtime)[0])
        listing = _get("/api/mobile/devices", runtime=runtime)
        body = _body(listing)
        assert listing.status == 200 and body["active_count"] == 1
        assert "grant_secret" not in json.dumps(body)
        assert body["devices"][0]["grant_id"] == claim["grant_id"]

    def test_management_is_owner_local_only(self, runtime):
        assert _get("/api/mobile/devices", runtime=runtime, client_host=PHONE_HOST).status == 403
        r = _post("/api/mobile/devices/revoke", {"device_id": "phone:x"}, runtime=runtime, client_host=PHONE_HOST)
        assert r.status == 403

    def test_revoke_all(self, runtime):
        _pair(runtime)
        _pair(runtime)
        r = _post("/api/mobile/devices/revoke", {"all": True, "reason": "panic"}, runtime=runtime)
        assert r.status == 200
        body = _body(_get("/api/mobile/devices", runtime=runtime))
        assert body["active_count"] == 0

    def test_receipt_chain_detects_tampering(self, runtime):
        _pair(runtime)
        service = mca.mobile_companion_service()
        path = service.store._receipts_path
        raw = path.read_text().splitlines()
        row = json.loads(raw[1])
        row["detail"] = {"falsified": "true"}
        raw[1] = json.dumps(row, sort_keys=True)
        path.write_text("\n".join(raw) + "\n")
        _, _, verified = service.store.receipts()
        assert verified is False

    def test_service_seam_routes_through_real_dispatch(self, runtime):
        from core.web.api.service import dispatch_get

        response = dispatch_get(
            path="/api/mobile/info", query={}, runtime=runtime, model_name="vool",
            client_host=PHONE_HOST,
        )
        assert response.status == 200
        payload = json.loads(response.body)
        assert payload["product"] == "vool-mobile-companion"
        assert "chats.send" in payload["actions"]
        assert "fs.read" not in payload["actions"]

    def test_unknown_mobile_route_is_typed_404(self, runtime):
        r = _get("/api/mobile/notaroute", runtime=runtime)
        assert r.status == 404


# ---------------------------------------------------------------------------
# Store-level invariants
# ---------------------------------------------------------------------------

class TestStore:
    def test_state_file_is_owner_only(self, runtime):
        _pair(runtime)
        state = mca._state_dir() / "state.json"
        assert oct(state.stat().st_mode & 0o777) == "0o600"

    def test_service_constructor_rehydrates_revoked_and_active(self, runtime):
        claim_a = _body(_pair(runtime)[0])
        claim_b = _body(_pair(runtime)[0])
        _post("/api/mobile/devices/revoke", {"device_id": claim_a["device_id"]}, runtime=runtime)
        fresh = MobileCompanionService(store=mca.mobile_companion_service().store)
        assert fresh.registry.is_revoked(claim_a["grant_id"])
        assert not fresh.registry.is_revoked(claim_b["grant_id"])
