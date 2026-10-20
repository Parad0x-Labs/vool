"""DROP-HYBRID-1 session protocol. UNREVIEWED: not for real messages.

Python implementation of the profile specified in
``third_party/drop-session/PROTOCOL.md``. It produces and accepts the same bytes as
the reference JavaScript package (see ``tests/fixtures/drop/``), built only from
the primitives in the ``cryptography`` package VOOL already ships: X25519,
Ed25519, ML-DSA-65, ML-KEM-768, HKDF-SHA256, HMAC-SHA256 and AES-256-GCM. No
primitive is implemented here.

Every function returns a new draft state; callers commit it only after the whole
operation succeeds, so a forged or failed frame never advances stored state.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa, mlkem, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROFILE = "DROP-HYBRID-1"
MAX_SAFE_INTEGER = 2**53 - 1
LIMITS = {
    "wireBytes": 110000, "plaintextBytes": 65536, "skipGap": 32, "skippedKeys": 64,
    "chainMessages": 4096, "sessionMessages": 8192, "sessionMs": 7 * 86400000,
    "prekeys": 32, "sessions": 64, "queued": 256,
}
_B64 = re.compile(r"[A-Za-z0-9_-]*")


class DropError(Exception):
    """A bounded refusal code. Never carries plaintext, keys or paths."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def demand(ok, code="INVALID_FORMAT"):
    if not ok:
        raise DropError(code)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(data)).decode("ascii").rstrip("=")


def unb64(value, size, maximum=None) -> bytes:
    maximum = size if maximum is None else maximum
    demand(isinstance(value, str) and _B64.fullmatch(value) is not None)
    demand(len(value) <= -(-maximum * 4 // 3))
    try:
        out = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as exc:
        raise DropError("INVALID_FORMAT") from exc
    demand(size <= len(out) <= maximum and b64(out) == value)
    return out


def _string(value: str) -> str:
    # Byte-for-byte ECMAScript JSON.stringify for strings (well-formed variant).
    out = ['"']
    for char in value:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char in "\b\f\n\r\t":
            out.append({"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}[char])
        elif code < 0x20 or 0xD800 <= code <= 0xDFFF:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def canonical(value, depth=0) -> str:
    """Sorted-key JSON: nonnegative safe integers only, no floats or other types."""
    demand(depth <= 12)
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int):
        demand(0 <= value <= MAX_SAFE_INTEGER)
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical(v, depth + 1) for v in value) + "]"
    demand(type(value) is dict and all(isinstance(k, str) for k in value))
    # ECMAScript sorts keys by UTF-16 code units.
    keys = sorted(value, key=lambda k: k.encode("utf-16-be", "surrogatepass"))
    return "{" + ",".join(_string(k) + ":" + canonical(value[k], depth + 1) for k in keys) + "}"


def encode(value) -> bytes:
    return canonical(value).encode("utf-8")


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        demand(key not in out, "NON_CANONICAL")
        out[key] = value
    return out


def _no_floats(_text):
    raise DropError("NON_CANONICAL")


def decode(wire: bytes):
    demand(isinstance(wire, (bytes, bytearray)) and len(wire) <= LIMITS["wireBytes"])
    try:
        obj = json.loads(bytes(wire).decode("utf-8"), object_pairs_hook=_no_duplicates,
                         parse_float=_no_floats, parse_constant=_no_floats)
        again = encode(obj)
    except DropError as exc:
        raise DropError("NON_CANONICAL" if exc.code == "NON_CANONICAL" else "INVALID_FORMAT") from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DropError("INVALID_FORMAT") from exc
    demand(again == bytes(wire), "NON_CANONICAL")
    return obj


def fields(value, names):
    demand(type(value) is dict and sorted(value) == sorted(names))


def integer(value, maximum=MAX_SAFE_INTEGER):
    demand(type(value) is int and 0 <= value <= maximum)
    return value


def clock(now):
    return integer(now)


def check_id(value):
    unb64(value, 16)
    return value


def fresh_id() -> str:
    return b64(os.urandom(16))


def digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hkdf(ikm: bytes, salt: bytes, info: bytes, size: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=size, salt=salt, info=info).derive(ikm)


def domain(label: str, data: bytes) -> bytes:
    return f"{PROFILE}/{label}\0".encode() + data


def equal(a: bytes, b: bytes) -> bool:
    return len(a) == len(b) and hmac.compare_digest(a, b)


_GENERATORS = {
    "x25519": x25519.X25519PrivateKey.generate,
    "ed25519": ed25519.Ed25519PrivateKey.generate,
    "ml-kem-768": mlkem.MLKEM768PrivateKey.generate,
    "ml-dsa-65": mldsa.MLDSA65PrivateKey.generate,
}
_PUBLIC_TYPES = {
    "x25519": x25519.X25519PublicKey, "ed25519": ed25519.Ed25519PublicKey,
    "ml-kem-768": mlkem.MLKEM768PublicKey, "ml-dsa-65": mldsa.MLDSA65PublicKey,
}
_PRIVATE_TYPES = {
    "x25519": x25519.X25519PrivateKey, "ed25519": ed25519.Ed25519PrivateKey,
    "ml-kem-768": mlkem.MLKEM768PrivateKey, "ml-dsa-65": mldsa.MLDSA65PrivateKey,
}
SPKI_SIZES = {"x25519": 44, "ed25519": 44, "ml-kem-768": 1206, "ml-dsa-65": 1974}
_SPKI = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
_PKCS8 = (serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def pair(kind: str) -> dict:
    key = _GENERATORS[kind]()
    return {"public": b64(key.public_key().public_bytes(*_SPKI)), "private": b64(key.private_bytes(*_PKCS8))}


def public_key(text, kind):
    data = unb64(text, SPKI_SIZES[kind])
    try:
        key = serialization.load_der_public_key(data)
    except (ValueError, TypeError) as exc:
        raise DropError("INVALID_KEY") from exc
    demand(isinstance(key, _PUBLIC_TYPES[kind]), "INVALID_KEY")
    demand(key.public_bytes(*_SPKI) == data, "INVALID_KEY")
    return key


def private_key(text, kind):
    try:
        key = serialization.load_der_private_key(unb64(text, 1, 5000), password=None)
    except (ValueError, TypeError) as exc:
        raise DropError("INVALID_KEY") from exc
    demand(isinstance(key, _PRIVATE_TYPES[kind]), "INVALID_KEY")
    return key


def dh(keypair: dict, remote: str) -> bytes:
    try:
        result = private_key(keypair["private"], "x25519").exchange(public_key(remote, "x25519"))
    except (DropError, ValueError) as exc:
        raise DropError("INVALID_KEY") from exc
    demand(result != bytes(32), "INVALID_KEY")
    return result


def identity() -> dict:
    return {"dh": pair("x25519"), "ed": pair("ed25519"), "pq": pair("ml-dsa-65")}


def public_identity(keys: dict) -> dict:
    return {"dh": keys["dh"]["public"], "ed": keys["ed"]["public"], "pq": keys["pq"]["public"]}


def check_identity(pub):
    fields(pub, ["dh", "ed", "pq"])
    public_key(pub["dh"], "x25519")
    public_key(pub["ed"], "ed25519")
    public_key(pub["pq"], "ml-dsa-65")


def fingerprint(pub) -> str:
    check_identity(pub)
    return b64(digest(domain("identity", encode(pub))))


def pin(pub, expected) -> str:
    got = fingerprint(pub)
    demand(equal(unb64(got, 32), unb64(expected, 32)), "IDENTITY_MISMATCH")
    return got


def signatures(keys, label, obj) -> dict:
    message = domain(label, encode(obj))
    return {"ed": b64(private_key(keys["ed"]["private"], "ed25519").sign(message)),
            "pq": b64(private_key(keys["pq"]["private"], "ml-dsa-65").sign(message))}


def check_signatures(pub, label, obj, sig):
    fields(sig, ["ed", "pq"])
    message = domain(label, encode(obj))
    try:
        public_key(pub["ed"], "ed25519").verify(unb64(sig["ed"], 64), message)
        public_key(pub["pq"], "ml-dsa-65").verify(unb64(sig["pq"], 3309), message)
    except InvalidSignature as exc:
        raise DropError("AUTH_FAILED") from exc


def seal(key: bytes, plaintext: bytes, aad: bytes, nonce: bytes | None = None,
         limit: int = LIMITS["plaintextBytes"]) -> dict:
    demand(isinstance(plaintext, (bytes, bytearray)) and len(plaintext) <= limit, "PAYLOAD_LIMIT")
    nonce = os.urandom(12) if nonce is None else nonce
    demand(len(key) == 32 and len(nonce) == 12)
    sealed = AESGCM(key).encrypt(nonce, bytes(plaintext), aad)
    return {"nonce": b64(nonce), "body": b64(sealed[:-16]), "tag": b64(sealed[-16:])}


def open_box(key: bytes, box, aad: bytes, limit: int = LIMITS["plaintextBytes"]) -> bytes:
    fields(box, ["nonce", "body", "tag"])
    nonce, tag = unb64(box["nonce"], 12), unb64(box["tag"], 16)
    body = unb64(box["body"], 0, limit)
    try:
        return AESGCM(key).decrypt(nonce, body + tag, aad)
    except InvalidTag as exc:
        raise DropError("AUTH_FAILED") from exc


def root_step(root: str, secret: bytes) -> tuple[str, str]:
    out = hkdf(secret, unb64(root, 32), domain("ratchet-root", b""), 64)
    return b64(out[:32]), b64(out[32:])


def chain_step(chain) -> tuple[str, bytes]:
    demand(chain is not None, "NO_SENDING_CHAIN")
    key = unb64(chain, 32)
    following = hmac.new(key, b"\x02", hashlib.sha256).digest()
    message = hmac.new(key, b"\x01", hashlib.sha256).digest()
    return b64(following), message


# -- Session drafts --------------------------------------------------------------

def make_prekey(keys, now, ttl=86400000) -> dict:
    clock(now)
    integer(ttl, LIMITS["sessionMs"])
    demand(ttl > 0, "EXPIRED")
    x, pq = pair("x25519"), pair("ml-kem-768")
    body = {"profile": PROFILE, "id": fresh_id(), "issued": now, "expires": now + ttl,
            "identity": public_identity(keys), "x": x["public"], "pq": pq["public"]}
    return {"x": x, "pq": pq, "bundle": {"body": body, "signatures": signatures(keys, "bundle", body)}}


def validate_bundle(bundle, expected_pin, now) -> str:
    clock(now)
    fields(bundle, ["body", "signatures"])
    body = bundle["body"]
    fields(body, ["profile", "id", "issued", "expires", "identity", "x", "pq"])
    demand(body["profile"] == PROFILE, "UNSUPPORTED_PROFILE")
    check_id(body["id"])
    peer = pin(body["identity"], expected_pin)
    integer(body["issued"])
    integer(body["expires"])
    demand(body["issued"] <= now < body["expires"]
           and body["expires"] - body["issued"] <= LIMITS["sessionMs"], "EXPIRED")
    public_key(body["x"], "x25519")
    public_key(body["pq"], "ml-kem-768")
    check_signatures(body["identity"], "bundle", body, bundle["signatures"])
    return peer


def _initial_root(parts, intro) -> str:
    return b64(hkdf(b"".join(parts), digest(domain("transcript", encode(intro))),
                    domain("initial-root", b""), 32))


def _state_for(intro, self_pin, peer, root, own_dh, remote_dh) -> dict:
    return {"profile": PROFILE, "sid": intro["sid"], "self": self_pin, "peer": peer,
            "expires": intro["expires"], "transcript": b64(digest(domain("transcript", encode(intro)))),
            "root": root, "ownDh": own_dh, "remoteDh": remote_dh, "sendChain": None,
            "receiveChain": None, "ns": 0, "nr": 0, "pn": 0, "total": 0, "skipped": {}}


def _alive(state, now):
    clock(now)
    demand(state["profile"] == PROFILE, "UNSUPPORTED_PROFILE")
    demand(now < state["expires"], "EXPIRED")
    demand(state["total"] < LIMITS["sessionMessages"], "SESSION_EXHAUSTED")


def _aad(state, header, sending) -> bytes:
    return domain("message", encode({
        "profile": PROFILE, "sid": state["sid"], "transcript": state["transcript"],
        "expires": state["expires"],
        "sender": state["self"] if sending else state["peer"],
        "recipient": state["peer"] if sending else state["self"], "header": header,
    }))


def send_draft(current, plaintext, now) -> tuple[dict, dict]:
    _alive(current, now)
    demand(isinstance(plaintext, (bytes, bytearray)) and len(plaintext) <= LIMITS["plaintextBytes"],
           "PAYLOAD_LIMIT")
    demand(current["ns"] < LIMITS["chainMessages"], "CHAIN_EXHAUSTED")
    state = copy.deepcopy(current)
    header = {"profile": PROFILE, "type": "message", "sid": state["sid"], "dh": state["ownDh"]["public"],
              "pn": state["pn"], "n": state["ns"]}
    following, key = chain_step(state["sendChain"])
    frame = {**header, "box": seal(key, plaintext, _aad(state, header, True))}
    state["sendChain"] = following
    state["ns"] += 1
    state["total"] += 1
    return state, frame


def check_frame(frame):
    fields(frame, ["profile", "type", "sid", "dh", "pn", "n", "box"])
    demand(frame["profile"] == PROFILE, "UNSUPPORTED_PROFILE")
    demand(frame["type"] == "message")
    check_id(frame["sid"])
    integer(frame["n"], LIMITS["chainMessages"] - 1)
    integer(frame["pn"], LIMITS["chainMessages"])
    public_key(frame["dh"], "x25519")
    fields(frame["box"], ["nonce", "body", "tag"])
    unb64(frame["box"]["nonce"], 12)
    unb64(frame["box"]["tag"], 16)
    unb64(frame["box"]["body"], 0, LIMITS["plaintextBytes"])


def _skip(state, until):
    demand(until - state["nr"] <= LIMITS["skipGap"], "SKIP_LIMIT")
    if state["receiveChain"] is None:
        demand(until == 0, "INVALID_COUNTER")
        return
    demand(until >= state["nr"], "REPLAY")
    demand(len(state["skipped"]) + until - state["nr"] <= LIMITS["skippedKeys"], "SKIP_LIMIT")
    while state["nr"] < until:
        following, key = chain_step(state["receiveChain"])
        state["skipped"][f"{state['remoteDh']}:{state['nr']}"] = b64(key)
        state["receiveChain"] = following
        state["nr"] += 1


def receive_draft(current, frame, now) -> tuple[dict, bytes]:
    _alive(current, now)
    check_frame(frame)
    demand(frame["sid"] == current["sid"], "WRONG_SESSION")
    state = copy.deepcopy(current)
    header = {k: frame[k] for k in ("profile", "type", "sid", "dh", "pn", "n")}
    slot = f"{frame['dh']}:{frame['n']}"
    if slot in state["skipped"]:
        key = unb64(state["skipped"].pop(slot), 32)
    else:
        if frame["dh"] != state["remoteDh"]:
            _skip(state, frame["pn"])
            state["pn"], state["ns"], state["nr"] = state["ns"], 0, 0
            state["remoteDh"] = frame["dh"]
            state["root"], state["receiveChain"] = root_step(state["root"], dh(state["ownDh"], state["remoteDh"]))
            state["ownDh"] = pair("x25519")
            state["root"], state["sendChain"] = root_step(state["root"], dh(state["ownDh"], state["remoteDh"]))
        _skip(state, frame["n"])
        state["receiveChain"], key = chain_step(state["receiveChain"])
        state["nr"] += 1
    plaintext = open_box(key, frame["box"], _aad(state, header, False))
    state["total"] += 1
    return state, plaintext


def _pin_self(keys) -> str:
    return b64(digest(domain("identity", encode(public_identity(keys)))))


def initiate_draft(keys, bundle, expected_pin, plaintext, now) -> tuple[dict, bytes]:
    recipient = validate_bundle(bundle, expected_pin, now)
    self_pin = pin(public_identity(keys), _pin_self(keys))
    demand(self_pin != recipient, "SELF_SESSION")
    ephemeral, ratchet = pair("x25519"), pair("x25519")
    shared, ciphertext = public_key(bundle["body"]["pq"], "ml-kem-768").encapsulate()
    intro = {"profile": PROFILE, "sid": fresh_id(), "prekey": bundle["body"]["id"],
             "bundleHash": b64(digest(encode(bundle))), "sender": public_identity(keys),
             "recipient": recipient, "ephemeral": ephemeral["public"], "kem": b64(ciphertext),
             "expires": bundle["body"]["expires"]}
    root = _initial_root([
        dh(keys["dh"], bundle["body"]["x"]), dh(ephemeral, bundle["body"]["identity"]["dh"]),
        dh(ephemeral, bundle["body"]["x"]), shared,
    ], intro)
    initial = _state_for(intro, self_pin, recipient, root, ratchet, bundle["body"]["x"])
    initial["root"], initial["sendChain"] = root_step(initial["root"], dh(ratchet, bundle["body"]["x"]))
    state, frame = send_draft(initial, plaintext, now)
    signed = {"intro": intro, "firstHash": b64(digest(encode(frame)))}
    wire = encode({"profile": PROFILE, "type": "init", "bundle": bundle, "intro": intro,
                   "message": frame, "signatures": signatures(keys, "init", signed)})
    demand(len(wire) <= LIMITS["wireBytes"], "PAYLOAD_LIMIT")
    return state, wire


def inspect_init(wire: bytes) -> dict:
    packet = decode(wire)
    fields(packet, ["profile", "type", "bundle", "intro", "message", "signatures"])
    demand(packet["profile"] == PROFILE, "UNSUPPORTED_PROFILE")
    demand(packet["type"] == "init")
    intro = packet["intro"]
    fields(intro, ["profile", "sid", "prekey", "bundleHash", "sender", "recipient", "ephemeral", "kem", "expires"])
    demand(intro["profile"] == PROFILE, "UNSUPPORTED_PROFILE")
    check_id(intro["sid"])
    check_id(intro["prekey"])
    unb64(intro["bundleHash"], 32)
    unb64(intro["recipient"], 32)
    unb64(intro["kem"], 1088)
    integer(intro["expires"])
    public_key(intro["ephemeral"], "x25519")
    check_frame(packet["message"])
    demand(packet["message"]["sid"] == intro["sid"] and packet["message"]["n"] == 0
           and packet["message"]["pn"] == 0, "INVALID_COUNTER")
    return packet


def accept_draft(keys, prekey, packet, expected_pin, now) -> tuple[dict, bytes]:
    intro, self_pin = packet["intro"], _pin_self(keys)
    demand(intro["recipient"] == self_pin, "IDENTITY_MISMATCH")
    peer = pin(intro["sender"], expected_pin)
    demand(peer != self_pin, "SELF_SESSION")
    validate_bundle(packet["bundle"], self_pin, now)
    demand(encode(prekey["bundle"]) == encode(packet["bundle"]), "UNKNOWN_PREKEY")
    demand(intro["prekey"] == prekey["bundle"]["body"]["id"]
           and intro["bundleHash"] == b64(digest(encode(packet["bundle"]))), "UNKNOWN_PREKEY")
    demand(intro["expires"] == packet["bundle"]["body"]["expires"], "EXPIRED")
    check_signatures(intro["sender"], "init",
                     {"intro": intro, "firstHash": b64(digest(encode(packet["message"])))}, packet["signatures"])
    try:
        shared = private_key(prekey["pq"]["private"], "ml-kem-768").decapsulate(unb64(intro["kem"], 1088))
    except ValueError as exc:
        raise DropError("AUTH_FAILED") from exc
    root = _initial_root([
        dh(prekey["x"], intro["sender"]["dh"]), dh(keys["dh"], intro["ephemeral"]),
        dh(prekey["x"], intro["ephemeral"]), shared,
    ], intro)
    return receive_draft(_state_for(intro, self_pin, peer, root, prekey["x"], None), packet["message"], now)
