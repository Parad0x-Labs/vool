"""Durable two-party Drop session client. UNREVIEWED: not for real messages.

Same API contract as the reference ``DropClient`` in third_party/drop-session:
every operation commits its state, queue entry and result in one transaction, a
retried request returns the stored ciphertext instead of re-encrypting, and a
forgotten session is refused forever. Decrypted text is data; this layer grants
no permission to act on it.
"""

from __future__ import annotations

import time

from core.drop.session import (
    LIMITS,
    accept_draft,
    b64,
    check_id,
    clock,
    decode,
    demand,
    digest,
    encode,
    fingerprint,
    fresh_id,
    identity,
    initiate_draft,
    inspect_init,
    make_prekey,
    public_identity,
    receive_draft,
    send_draft,
    unb64,
    validate_bundle,
)
from core.drop.store import EncryptedStore


def _now() -> int:
    return int(time.time() * 1000)


def _bytes(value, maximum, code) -> bytes:
    demand(isinstance(value, (bytes, bytearray)) and len(value) <= maximum, code)
    return bytes(value)


class DropClient:
    def __init__(self, directory, vault_key: bytes):
        self._closed = False
        self._store = EncryptedStore(directory, vault_key)
        try:
            self._identity = self._store.transaction(self._load_identity)
            self._public = public_identity(self._identity)
            self._pin = fingerprint(self._public)
        except BaseException:
            self.close()
            raise

    def _load_identity(self):
        prior = self._store.get("identity", "self")
        if prior:
            return prior
        keys = identity()
        self._store.put("identity", "self", keys)
        return keys

    def _ready(self):
        demand(not self._closed, "CLIENT_CLOSED")

    @property
    def fingerprint(self) -> str:
        self._ready()
        return self._pin

    def issue_invite(self, *, now=None, ttl=86400000) -> bytes:
        self._ready()
        now = _now() if now is None else now

        def run():
            record = make_prekey(self._identity, now, ttl)
            self._store.put("prekey", record["bundle"]["body"]["id"], record)
            return encode(record["bundle"])
        return self._store.transaction(run)

    def discard_invite(self, prekey_id):
        self._ready()
        check_id(prekey_id)
        self._store.transaction(lambda: self._store.remove("prekey", prekey_id))

    def _unrevoked(self, session):
        demand(not self._store.get("revoked", session), "SESSION_REVOKED")

    def start(self, *, invite, peer_fingerprint, plaintext, request=None, now=None) -> dict:
        self._ready()
        request = fresh_id() if request is None else check_id(request)
        now = clock(_now() if now is None else now)
        plaintext = _bytes(plaintext, LIMITS["plaintextBytes"], "PAYLOAD_LIMIT")
        invite = _bytes(invite, LIMITS["wireBytes"], "INVALID_FORMAT")
        bundle = decode(invite)
        validate_bundle(bundle, peer_fingerprint, now)
        binding = b64(digest(encode({"type": "start", "inviteHash": b64(digest(invite)),
                                     "peerFingerprint": peer_fingerprint,
                                     "payloadHash": b64(digest(plaintext))})))

        def run():
            old = self._store.get("outbox", request)
            if old:
                demand(old["binding"] == binding, "REQUEST_CONFLICT")
                return {"request": request, "session": old["session"],
                        "wire": unb64(old["wire"], 1, LIMITS["wireBytes"])}
            state, wire = initiate_draft(self._identity, bundle, peer_fingerprint, plaintext, now)
            demand(not self._store.get("revoked", state["sid"]), "SESSION_REVOKED")
            self._store.put("session", state["sid"], state)
            self._store.put("outbox", request, {"session": state["sid"], "wire": b64(wire), "binding": binding})
            return {"request": request, "session": state["sid"], "wire": wire}
        return self._store.transaction(run)

    def send(self, *, session, plaintext, request=None, now=None) -> dict:
        self._ready()
        check_id(session)
        request = fresh_id() if request is None else check_id(request)
        now = clock(_now() if now is None else now)
        plaintext = _bytes(plaintext, LIMITS["plaintextBytes"], "PAYLOAD_LIMIT")
        binding = b64(digest(encode({"type": "send", "session": session, "payloadHash": b64(digest(plaintext))})))

        def run():
            self._unrevoked(session)
            old = self._store.get("outbox", request)
            if old:
                demand(old["binding"] == binding, "REQUEST_CONFLICT")
                return {"request": request, "session": session, "wire": unb64(old["wire"], 1, LIMITS["wireBytes"])}
            state = self._store.get("session", session)
            demand(state, "UNKNOWN_SESSION")
            state, frame = send_draft(state, plaintext, now)
            wire = encode(frame)
            self._store.put("session", session, state)
            self._store.put("outbox", request, {"session": session, "wire": b64(wire), "binding": binding})
            return {"request": request, "session": session, "wire": wire}
        return self._store.transaction(run)

    def accept(self, *, wire, peer_fingerprint, now=None) -> dict:
        self._ready()
        now = clock(_now() if now is None else now)
        wire = _bytes(wire, LIMITS["wireBytes"], "INVALID_FORMAT")
        packet = inspect_init(wire)
        session = packet["intro"]["sid"]

        def run():
            self._unrevoked(session)
            demand(not self._store.get("session", session), "REPLAY")
            prekey = self._store.get("prekey", packet["intro"]["prekey"])
            demand(prekey, "PREKEY_UNAVAILABLE")
            state, plaintext = accept_draft(self._identity, prekey, packet, peer_fingerprint, now)
            self._store.put("session", session, state)
            delivery = self._record_inbox(session, wire, plaintext)
            self._store.remove("prekey", packet["intro"]["prekey"])
            return {"session": session, "delivery": delivery, "plaintext": plaintext}
        return self._store.transaction(run)

    def receive(self, *, wire, now=None) -> dict:
        self._ready()
        now = clock(_now() if now is None else now)
        wire = _bytes(wire, LIMITS["wireBytes"], "INVALID_FORMAT")
        frame = decode(wire)
        demand(type(frame) is dict)
        check_id(frame.get("sid"))

        def run():
            self._unrevoked(frame["sid"])
            state = self._store.get("session", frame["sid"])
            demand(state, "UNKNOWN_SESSION")
            state, plaintext = receive_draft(state, frame, now)
            self._store.put("session", frame["sid"], state)
            delivery = self._record_inbox(frame["sid"], wire, plaintext)
            return {"session": frame["sid"], "delivery": delivery, "plaintext": plaintext}
        return self._store.transaction(run)

    def _record_inbox(self, session, wire, plaintext) -> str:
        delivery = b64(digest(wire))
        demand(not self._store.get("inbox", delivery), "REPLAY")
        self._store.put("inbox", delivery, {"session": session, "plaintext": b64(plaintext)})
        return delivery

    def pending_sends(self) -> list[dict]:
        self._ready()

        def run():
            out = []
            for request, value in self._store.list("outbox"):
                self._unrevoked(value["session"])
                out.append({"request": request, "session": value["session"],
                            "wire": unb64(value["wire"], 1, LIMITS["wireBytes"])})
            return out
        return self._store.transaction(run)

    def acknowledge_send(self, request):
        self._ready()
        check_id(request)
        self._store.transaction(lambda: self._store.remove("outbox", request))

    def pending_receives(self) -> list[dict]:
        self._ready()

        def run():
            out = []
            for delivery, value in self._store.list("inbox"):
                self._unrevoked(value["session"])
                out.append({"delivery": delivery, "session": value["session"],
                            "plaintext": unb64(value["plaintext"], 0, LIMITS["plaintextBytes"])})
            return out
        return self._store.transaction(run)

    def acknowledge_receive(self, delivery):
        self._ready()
        unb64(delivery, 32)
        self._store.transaction(lambda: self._store.remove("inbox", delivery))

    def forget_session(self, session):
        self._ready()
        check_id(session)

        def run():
            self._store.put("revoked", session, {"revoked": True})
            self._store.remove("session", session)
            self._store.remove_session_queues(session)
        self._store.transaction(run)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._identity = None
        self._public = None
        if getattr(self, "_store", None) is not None:
            self._store.close()
