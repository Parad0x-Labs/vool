"""DROP-HYBRID-1 in Python: byte compatibility with the reference and its failure paths.

UNREVIEWED protocol. These pin behavior; they are not a security review.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import pytest

from core.drop.client import DropClient
from core.drop.session import (
    DropError,
    accept_draft,
    b64,
    canonical,
    decode,
    inspect_init,
    receive_draft,
    unb64,
)

FIXTURE = Path(__file__).parent / "fixtures" / "drop" / "js_transcript.json"
NOW = 1791000000000


def _wire(text):
    return unb64(text, 1, 110000)


def test_python_reads_the_reference_javascript_transcript():
    vectors = json.loads(FIXTURE.read_text())
    for case in vectors["canonical"]:
        assert canonical(case["value"]) == case["text"], case["name"]
    packet = inspect_init(_wire(vectors["initWire"]))
    state, plaintext = accept_draft(vectors["bobKeys"], vectors["bobPrekey"], packet,
                                    vectors["alicePin"], vectors["now"])
    assert plaintext.decode() == vectors["initPlaintext"]
    # The receiving half is fixed by the transcript; the sending half uses the
    # responder's fresh random ratchet key, so it legitimately differs.
    for name in ("sid", "transcript", "remoteDh", "receiveChain", "nr", "total"):
        assert state[name] == vectors["bobStateAfterAccept"][name], name
    for side, start in (("bobReceives", "bobStateAfterAccept"), ("aliceReceives", "aliceStateAfterInit")):
        state = vectors[start]
        for index in vectors[side]:
            frame = vectors["frames"][index]
            state, plaintext = receive_draft(state, decode(_wire(frame["wire"])), vectors["now"] + 100)
            assert plaintext.decode() == frame["plaintext"]


@pytest.fixture
def clients(tmp_path):
    keys = {"a": secrets.token_bytes(32), "b": secrets.token_bytes(32)}
    os.chmod(tmp_path, 0o700)
    opened = []

    def open_client(name):
        client = DropClient(tmp_path / name, keys[name])
        opened.append(client)
        return client

    yield open_client
    for client in opened:
        client.close()


def _session(open_client):
    a, b = open_client("a"), open_client("b")
    invite = b.issue_invite(now=NOW)
    started = a.start(invite=invite, peer_fingerprint=b.fingerprint, plaintext=b"hello", now=NOW)
    accepted = b.accept(wire=started["wire"], peer_fingerprint=a.fingerprint, now=NOW)
    assert accepted["plaintext"] == b"hello"
    return a, b, started["session"]


def test_ratchets_out_of_order_and_across_restart(clients):
    a, b, sid = _session(clients)
    for round_ in range(3):
        first = b.send(session=sid, plaintext=f"b{round_}.0".encode(), now=NOW)
        second = b.send(session=sid, plaintext=f"b{round_}.1".encode(), now=NOW)
        assert a.receive(wire=second["wire"], now=NOW)["plaintext"] == f"b{round_}.1".encode()
        assert a.receive(wire=first["wire"], now=NOW)["plaintext"] == f"b{round_}.0".encode()
        reply = a.send(session=sid, plaintext=f"a{round_}".encode(), now=NOW)
        a.close()
        a = clients("a")
        assert b.receive(wire=reply["wire"], now=NOW)["plaintext"] == f"a{round_}".encode()


def _tampered(wire, mutate):
    frame = json.loads(wire)
    mutate(frame)
    return canonical(frame).encode()


def test_forged_frames_never_advance_state(clients):
    a, b, sid = _session(clients)
    genuine = b.send(session=sid, plaintext=b"genuine", now=NOW)["wire"]

    def flip_body(frame):
        body = bytearray(unb64(frame["box"]["body"], 0, 65536))
        body[0] ^= 1
        frame["box"]["body"] = b64(body)

    def bump_counter(frame):
        frame["n"] += 1

    def other_ratchet_key(frame):
        frame["dh"] = json.loads(b.send(session=sid, plaintext=b"x", now=NOW)["wire"])["dh"]
        frame["pn"] = 5

    for mutate, code in ((flip_body, "AUTH_FAILED"), (bump_counter, "AUTH_FAILED")):
        with pytest.raises(DropError, match=code):
            a.receive(wire=_tampered(genuine, mutate), now=NOW)
    with pytest.raises(DropError):
        a.receive(wire=_tampered(genuine, other_ratchet_key), now=NOW)
    a.close()
    a = clients("a")
    assert a.receive(wire=genuine, now=NOW)["plaintext"] == b"genuine"
    with pytest.raises(DropError, match="REPLAY"):
        a.receive(wire=genuine, now=NOW)
    (delivery,) = [r["delivery"] for r in a.pending_receives()]
    a.acknowledge_receive(delivery)
    with pytest.raises(DropError, match="REPLAY"):
        a.receive(wire=genuine, now=NOW)


def test_first_message_needs_both_signatures_the_pinned_peer_and_a_live_prekey(clients):
    a, b = clients("a"), clients("b")
    invite = b.issue_invite(now=NOW)
    started = a.start(invite=invite, peer_fingerprint=b.fingerprint, plaintext=b"hi", now=NOW)
    wire = started["wire"]
    with pytest.raises(DropError, match="IDENTITY_MISMATCH"):
        b.accept(wire=wire, peer_fingerprint=b.fingerprint, now=NOW)
    without_pq = _tampered(wire, lambda f: f["signatures"].pop("pq"))
    with pytest.raises(DropError, match="INVALID_FORMAT"):
        b.accept(wire=without_pq, peer_fingerprint=a.fingerprint, now=NOW)

    def bad_ed(frame):
        sig = bytearray(unb64(frame["signatures"]["ed"], 64))
        sig[0] ^= 1
        frame["signatures"]["ed"] = b64(sig)
    with pytest.raises(DropError, match="AUTH_FAILED"):
        b.accept(wire=_tampered(wire, bad_ed), peer_fingerprint=a.fingerprint, now=NOW)
    # None of the refusals consumed the one-time prekey.
    assert b.accept(wire=wire, peer_fingerprint=a.fingerprint, now=NOW)["plaintext"] == b"hi"
    with pytest.raises(DropError, match="REPLAY"):
        b.accept(wire=wire, peer_fingerprint=a.fingerprint, now=NOW)
    second = a.start(invite=invite, peer_fingerprint=b.fingerprint, plaintext=b"again", now=NOW)
    with pytest.raises(DropError, match="PREKEY_UNAVAILABLE"):
        b.accept(wire=second["wire"], peer_fingerprint=a.fingerprint, now=NOW)


def test_expired_invite_and_session_are_refused(clients):
    a, b = clients("a"), clients("b")
    invite = b.issue_invite(now=NOW, ttl=1000)
    with pytest.raises(DropError, match="EXPIRED"):
        a.start(invite=invite, peer_fingerprint=b.fingerprint, plaintext=b"late", now=NOW + 1000)
    a, b, sid = _session(clients)
    with pytest.raises(DropError, match="EXPIRED"):
        a.send(session=sid, plaintext=b"late", now=NOW + 86400000)


def test_bounds_fail_closed(clients):
    a, b, sid = _session(clients)
    with pytest.raises(DropError, match="PAYLOAD_LIMIT"):
        a.send(session=sid, plaintext=bytes(65537), now=NOW)
    frames = [a.send(session=sid, plaintext=b"%d" % i, now=NOW)["wire"] for i in range(34)]
    with pytest.raises(DropError, match="SKIP_LIMIT"):
        b.receive(wire=frames[33], now=NOW)
    assert b.receive(wire=frames[32], now=NOW)["plaintext"] == b"32"


@pytest.mark.parametrize("wire", [
    b'{"a":1,"a":1}', b'{"a": 1}', b'{"a":1.0}', b'{"a":NaN}', b'{"a":9007199254740992}',
    b'{"b":1,"a":2}', b'{"a":"\xff"}', b'{"a":-0}', b"[1, 2]",
])
def test_decoder_accepts_only_the_canonical_encoding(wire):
    with pytest.raises(DropError):
        decode(wire)


def test_forget_is_durable_and_removes_queued_content(clients):
    a, b, sid = _session(clients)
    pending = b.send(session=sid, plaintext=b"queued", now=NOW)
    a.receive(wire=pending["wire"], now=NOW)
    a.forget_session(sid)
    assert a.pending_receives() == []
    a.close()
    a = clients("a")
    with pytest.raises(DropError, match="SESSION_REVOKED"):
        a.receive(wire=b.send(session=sid, plaintext=b"after", now=NOW)["wire"], now=NOW)
    with pytest.raises(DropError, match="SESSION_REVOKED"):
        a.send(session=sid, plaintext=b"after", now=NOW)


def test_retry_returns_the_stored_ciphertext_and_a_changed_retry_is_refused(clients):
    a, b, sid = _session(clients)
    request = b64(secrets.token_bytes(16))
    first = a.send(session=sid, plaintext=b"once", request=request, now=NOW)
    assert a.send(session=sid, plaintext=b"once", request=request, now=NOW)["wire"] == first["wire"]
    with pytest.raises(DropError, match="REQUEST_CONFLICT"):
        a.send(session=sid, plaintext=b"twice", request=request, now=NOW)
    assert b.receive(wire=first["wire"], now=NOW)["plaintext"] == b"once"


def test_wrong_vault_key_and_unsafe_store_are_refused(tmp_path):
    os.chmod(tmp_path, 0o700)
    key = secrets.token_bytes(32)
    DropClient(tmp_path / "home", key).close()
    with pytest.raises(DropError, match="CORRUPT_STORE_OR_WRONG_KEY"):
        DropClient(tmp_path / "home", secrets.token_bytes(32))
    reopened = DropClient(tmp_path / "home", key)
    reopened.close()
    (tmp_path / "open").mkdir(mode=0o755)
    os.chmod(tmp_path / "open", 0o755)
    with pytest.raises(DropError, match="UNSAFE_STORE_PERMISSIONS"):
        DropClient(tmp_path / "open", key)
    (tmp_path / "link").symlink_to(tmp_path / "home")
    with pytest.raises(DropError, match="UNSAFE_STORE_PATH"):
        DropClient(tmp_path / "link", key)
