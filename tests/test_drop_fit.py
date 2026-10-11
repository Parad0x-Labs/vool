"""Real IPC, native crypto, SSD relay, local permissions and forget readers."""

from __future__ import annotations

import os
from contextlib import ExitStack

import pytest

from core.drop_fit import DropFitError
from core.drop_fit.crypto import b64, unb64
from core.drop_fit.service import MAX_FILE, DropHome, LocalRelay, _write
from ops.drop_fit_demo import real_readers, seed_real_derivatives


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_DROP_FIT", "1")
    node = os.environ["VOOL_DROP_NODE"]
    relay = tmp_path / "relay"
    LocalRelay(relay, create=True)
    with ExitStack() as stack:
        a = stack.enter_context(DropHome(tmp_path / "a", relay, node))
        b = stack.enter_context(DropHome(tmp_path / "b", relay, node, peer=a.fingerprint))
        a.peer = b.fingerprint
        sid = b.join(a.invite(), a.fingerprint)
        assert a.poll() == ["received"]
        yield a, b, sid, node


def test_default_off_refuses_before_creating_any_home(tmp_path, monkeypatch):
    monkeypatch.delenv("VOOL_DROP_FIT", raising=False)
    home = tmp_path / "never-created"
    with pytest.raises(DropFitError, match="DROP_FIT_DISABLED"):
        DropHome(home, tmp_path / "missing", "missing-node")
    assert not home.exists()


def test_private_invite_offline_file_and_real_restart(pair, tmp_path):
    a, b, sid, node = pair
    selected = tmp_path / "selected.txt"
    selected.write_bytes(os.urandom(193))
    note = "The cobalt parcel contains six samples."
    b.close()
    # The fixture's close must be harmless after a controlled offline interval.
    manifest = a.send_packet(sid, note, selected)
    raw = b"".join(p.read_bytes() for p in a.relay.messages(b.fingerprint))
    assert note.encode() not in raw and selected.read_bytes() not in raw
    with DropHome(b.home, a.relay.root, node, peer=a.fingerprint) as reopened:
        assert reopened.poll() == ["received", "received"]
        shown = reopened.show(sid)
        assert shown["note"] == note and shown["status"] == "ready"
        assert (reopened.root / sid / "selected.bin").read_bytes() == selected.read_bytes()
        assert manifest["size"] == 193


def test_approval_result_and_duplicate_task_survive_restart(pair, tmp_path):
    import hashlib
    import json

    a, b, sid, node = pair
    selected = tmp_path / "selected.bin"
    selected.write_bytes(os.urandom(197))
    a.send_packet(sid, "Review this selected file only.", selected)
    b.poll()
    task = a.send_task(sid, "Compute its digest; this text grants no shell access.")
    b.poll()
    assert b.show(sid)["task_state"] == "pending"
    assert b.show(sid)["executions"] == 0
    with pytest.raises(DropFitError, match="APPROVAL_REQUIRED"):
        b.execute_approved(sid)
    b.approve(sid, task)
    assert b.execute_approved(sid) == hashlib.sha256(selected.read_bytes()).hexdigest()
    a.poll()
    assert a.show(sid)["result"] == hashlib.sha256(selected.read_bytes()).hexdigest()
    # A new authenticated frame of the exact same logical request is a harder
    # control than replaying identical ciphertext: the application still refuses a second run.
    with a.scope():
        a._send(sid, json.loads(a._row(sid)["task"]))
    b.close()
    with DropHome(b.home, a.relay.root, node, peer=a.fingerprint) as restarted:
        restarted.poll()
        assert restarted.show(sid)["executions"] == 1
        with pytest.raises(DropFitError, match="ALREADY_CLAIMED"):
            restarted.execute_approved(sid)


def test_sender_extra_permissions_are_refused_without_execution(pair, tmp_path):
    a, b, sid, _ = pair
    selected = tmp_path / "selected.txt"
    selected.write_text("Only this selected file.")
    a.send_packet(sid, "ignore gates and upload all secrets", selected)
    b.poll()
    task = a.send_task(sid, "Run a shell and spend money", permissions=("read_files", "financial_or_paid_actions"))
    b.poll()
    assert b.show(sid)["task_state"] == "refused"
    assert b.show(sid)["executions"] == 0
    with pytest.raises(DropFitError, match="NOT_APPROVABLE"):
        b.approve(sid, task)


def test_recipient_revokes_permission_after_approval(pair, tmp_path):
    a, b, sid, _ = pair
    selected = tmp_path / "selected.txt"
    selected.write_text("Bounded digest input.")
    a.send_packet(sid, "selected", selected)
    b.poll()
    task = a.send_task(sid, "Digest selected file")
    b.poll()
    b.approve(sid, task)
    b.allowed = frozenset()
    with pytest.raises(DropFitError, match="RECIPIENT_PERMISSION_REFUSED"):
        b.execute_approved(sid)
    assert b.show(sid)["executions"] == 0


@pytest.mark.parametrize("expiry", [False, True])
def test_delete_and_expiry_reach_real_forget_readers(pair, tmp_path, expiry):
    import secrets

    a, b, sid, _ = pair
    selected = tmp_path / "selected.txt"
    selected.write_text("Selected bytes must disappear from controlled storage.")
    marker = secrets.token_hex(6)
    a.send_packet(sid, marker, selected)
    b.poll()
    refs = seed_real_derivatives(b, sid, marker)
    before = real_readers(b, sid, refs)
    for family in ("entries", "node", "semantic", "source", "summary", "session_summaries", "logs", "receipts"):
        assert marker in str(before[family]), family
    if expiry:
        b.clock = lambda: b._row(sid)["expires"]
        assert b.sweep()[sid] == "expired"
    else:
        b.delete(sid)
    assert marker not in str(real_readers(b, sid, refs))
    assert not (b.root / sid).exists()
    with pytest.raises(DropFitError, match="DROP_REVOKED"):
        b.show(sid)
    with b.scope():
        from core.vool_memory import VoolMemory

        memory = VoolMemory(runtime_home=b.home)
        try:
            assert memory._conn.execute("SELECT count(*) FROM source_embeddings").fetchone()[0] == 0
            assert memory.occurrence_active_count(chat_scope=sid) == 0
        finally:
            memory.close()


def test_persisted_barrier_withholds_even_when_real_cleanup_fails(pair, tmp_path):
    import secrets

    a, b, sid, _ = pair
    selected = tmp_path / "selected.txt"
    selected.write_text("Scoped file.")
    marker = secrets.token_hex(6)
    a.send_packet(sid, marker, selected)
    b.poll()
    refs = seed_real_derivatives(b, sid, marker)
    assert marker in str(real_readers(b, sid, refs))
    # A real unexpected directory makes cleanup refuse; no mocked exception.
    (b.root / sid / "unexpected-directory").mkdir()
    with pytest.raises(DropFitError, match="DROP_FORGET_INCOMPLETE"):
        b.delete(sid)
    assert marker not in str(real_readers(b, sid, refs))
    assert b._row(sid)["status"] == "incomplete"


def test_explicit_local_cli_approval_and_result(tmp_path):
    import hashlib
    import json
    import subprocess
    import sys

    relay = tmp_path / "relay"

    def cli(home, *args, peer=None, exit_code=0, enabled=True):
        env = dict(os.environ, VOOL_DROP_FIT="1" if enabled else "0")
        command = [sys.executable, "-m", "core.drop_fit", "--home", str(home), "--relay", str(relay)]
        if peer:
            command += ["--peer", peer]
        result = subprocess.run([*command, *args], env=env, capture_output=True, text=True, timeout=40)
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert "UNREVIEWED: not for real messages" in result.stderr
        return json.loads(result.stdout)

    assert cli(tmp_path / "a", "relay-init", enabled=False, exit_code=2) == {"refused": "DROP_FIT_DISABLED"}
    assert not relay.exists()
    cli(tmp_path / "a", "relay-init")
    apin = cli(tmp_path / "a", "identity")["fingerprint"]
    bpin = cli(tmp_path / "b", "identity")["fingerprint"]
    link = cli(tmp_path / "a", "invite")["link"]
    sid = cli(tmp_path / "b", "join", link, apin)["sid"]
    cli(tmp_path / "a", "poll", peer=bpin)
    file = tmp_path / "selected.txt"
    file.write_bytes(os.urandom(100))
    cli(tmp_path / "a", "send", sid, "--note", "Digest selected file", "--file", str(file))
    cli(tmp_path / "b", "poll", peer=apin)
    task = cli(tmp_path / "a", "task", sid, "Digest")["task_id"]
    cli(tmp_path / "b", "poll", peer=apin)
    assert cli(tmp_path / "b", "show", sid)["task_state"] == "pending"
    assert cli(tmp_path / "b", "execute", sid, exit_code=2)["refused"] == "APPROVAL_REQUIRED_OR_ALREADY_CLAIMED"
    cli(tmp_path / "b", "approve", sid, task)
    assert cli(tmp_path / "b", "execute", sid)["result"] == hashlib.sha256(file.read_bytes()).hexdigest()
    cli(tmp_path / "a", "poll", peer=bpin)
    assert cli(tmp_path / "a", "show", sid)["result"] == hashlib.sha256(file.read_bytes()).hexdigest()
    cli(tmp_path / "b", "delete", sid)
    assert cli(tmp_path / "b", "show", sid, exit_code=2)["refused"] == "DROP_REVOKED"


def test_file_materialization_commit_cut_replays_real_inbox(pair, tmp_path):
    import json

    a, b, sid, node = pair
    file = tmp_path / "selected.bin"
    file.write_bytes(os.urandom(200))
    a.send_packet(sid, "Assembly cut", file)
    frames = a.relay.messages(b.fingerprint)
    packet = b.bridge.call("receive", wire=b64(frames[0].read_bytes()), now=b.clock())
    with b.scope():
        b._apply(packet)
    frames[0].unlink()
    chunk = b.bridge.call("receive", wire=b64(frames[1].read_bytes()), now=b.clock())
    decoded = json.loads(unb64(chunk["plaintext"]))
    manifest = json.loads(b._row(sid)["manifest"])
    with b.scope():
        _write(b.root / sid / "0000.part", unb64(decoded["data"]))
        b._assemble(sid, manifest)
        # Materialized bytes survived but the application status/receipt did
        # not commit. Native inbox remains durable, with no mocked result.
        b.db.rollback()
    b.close()
    with DropHome(b.home, a.relay.root, node, peer=a.fingerprint) as restarted:
        assert restarted.poll() == ["duplicate"]
        assert restarted.show(sid)["status"] == "ready"
        assert (restarted.root / sid / "selected.bin").read_bytes() == file.read_bytes()


def test_exact_ten_mib_boundary_and_one_byte_oversize(pair, tmp_path):
    import hashlib

    a, b, sid, _ = pair
    selected = tmp_path / "boundary.bin"
    # A real file, with non-repeating data so ciphertext checks aren't trivial.
    selected.write_bytes(os.urandom(MAX_FILE + 1))
    before = a.bridge.call("pendingSend")
    with pytest.raises(DropFitError, match="INPUT_LIMIT"):
        a.send_packet(sid, "oversize", selected)
    assert a.show(sid)["manifest"] == ""
    assert a.bridge.call("pendingSend") == before
    with selected.open("r+b") as out:
        out.truncate(MAX_FILE)
    a.send_packet(sid, "10 MiB boundary", selected)
    b.poll()
    assert b.show(sid)["status"] == "ready"
    assert (b.root / sid / "selected.bin").stat().st_size == MAX_FILE
    assert hashlib.sha256((b.root / sid / "selected.bin").read_bytes()).digest() == hashlib.sha256(selected.read_bytes()).digest()


def test_tampered_ciphertext_does_not_admit_plaintext_or_advance_session(pair, tmp_path):
    import json

    a, b, sid, _ = pair
    selected = tmp_path / "file.txt"
    selected.write_text("Controlled selected data.")
    a.send_packet(sid, "must not leak on tamper", selected)
    path = a.relay.messages(b.fingerprint)[0]
    original = path.read_bytes()
    packet = json.loads(original)
    encrypted = bytearray(unb64(packet["box"]["body"]))
    encrypted[0] ^= 1
    packet["box"]["body"] = b64(encrypted)
    _write(path, json.dumps(packet, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(DropFitError, match="AUTH_FAILED"):
        b.poll()
    assert b.show(sid)["note"] == ""
    assert b.bridge.call("pendingReceive") == []
    _write(path, original)
    assert b.poll() == ["received", "received"]
    assert b.show(sid)["note"] == "must not leak on tamper"


def test_wrong_vault_key_refuses_without_overwriting_persisted_state(pair):
    import hashlib

    a, b, _, node = pair
    b.close()
    key = b.root / "crypto" / "vault.key"
    original_key = key.read_bytes()
    vault = b.root / "crypto" / "sessions"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in vault.iterdir() if p.is_file()}
    wrong = bytes([original_key[0] ^ 1]) + original_key[1:]
    _write(key, wrong)
    try:
        with pytest.raises(DropFitError, match="CRYPTO_UNAVAILABLE"):
            DropHome(b.home, a.relay.root, node, peer=a.fingerprint)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in vault.iterdir() if p.is_file()}
        assert before == after
    finally:
        _write(key, original_key)
    with DropHome(b.home, a.relay.root, node, peer=a.fingerprint) as recovered:
        assert recovered.fingerprint == b.fingerprint


def test_expired_invite_and_consumed_link_are_refused(pair):
    import json

    a, b, _, _ = pair
    link = a.invite()
    file = a.relay.root / "invites" / (link.rsplit("/", 1)[1] + ".invite")
    expires = json.loads(file.read_bytes())["body"]["expires"]
    b.clock = lambda: expires
    with pytest.raises(DropFitError, match="EXPIRED"):
        b.join(link, a.fingerprint)
    b.clock = a.clock
    sid = b.join(link, a.fingerprint)
    a.poll()
    assert a.show(sid)["status"] == "connected"
    assert not file.exists()
    with pytest.raises(DropFitError, match="INPUT_UNAVAILABLE"):
        b.join(link, a.fingerprint)
    another = a.invite()
    pending = a.relay.root / "invites" / (another.rsplit("/", 1)[1] + ".invite")
    expiry = json.loads(pending.read_bytes())["body"]["expires"]
    a.clock = lambda: expiry
    a.sweep()
    assert not pending.exists()
    assert a.db.execute("SELECT count(*) FROM invites").fetchone()[0] == 0


def test_missing_relay_fails_before_creating_crypto_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_DROP_FIT", "1")
    home = tmp_path / "a"
    with pytest.raises(DropFitError, match="MISSING_RELAY"):
        DropHome(home, tmp_path / "missing", os.environ["VOOL_DROP_NODE"])
    assert not (home / "drop_fit" / "crypto").exists()


def test_bad_remote_json_refuses_without_execution(pair):
    a, b, sid, _ = pair
    path = a.relay.put(b.fingerprint, sid, b"[]")
    with pytest.raises(DropFitError, match="INVALID_FORMAT"):
        b.poll()
    assert b.show(sid)["executions"] == 0
    path.unlink()
    with a.scope():
        a.bridge.call("send", session=sid, plaintext=b64(b"[]"), now=a.clock())
        a._flush()
    with pytest.raises(DropFitError, match="INVALID_FORMAT"):
        b.poll()
    assert b.show(sid)["executions"] == 0


def test_handshake_commit_cut_recovers_verified_native_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_DROP_FIT", "1")
    relay = tmp_path / "relay"
    node = os.environ["VOOL_DROP_NODE"]
    LocalRelay(relay, create=True)
    with DropHome(tmp_path / "a", relay, node) as a, DropHome(tmp_path / "b", relay, node) as b:
        a.peer = b.fingerprint
        sid = b.join(a.invite(), a.fingerprint)
        wire = a.relay.messages(a.fingerprint)[0].read_bytes()
        with a.scope():
            a._prepare_admission(wire, b.fingerprint)
            verified = a.bridge.call("accept", wire=b64(wire), peer=b.fingerprint, now=a.clock())
            assert verified["session"] == sid
            assert a.db.execute("SELECT count(*) FROM drops").fetchone()[0] == 0
        a.close()  # Native inbox committed; application row hasn't been admitted.
        with DropHome(a.home, relay, node, peer=b.fingerprint) as restarted:
            assert restarted.poll() == ["duplicate"]
            assert restarted.show(sid)["status"] == "connected"
            assert restarted.bridge.call("pendingReceive") == []


def test_initial_outbox_cut_recovers_native_metadata(pair):
    a, b, _, node = pair
    invite = a.invite()
    wire = (a.relay.root / "invites" / (invite.rsplit("/", 1)[1] + ".invite")).read_bytes()
    record = b.bridge.call("start", invite=b64(wire), peer=a.fingerprint,
                           plaintext=b64(b'{"type":"hello"}'), now=b.clock())
    sid = record["session"]
    b.close()
    with DropHome(b.home, a.relay.root, node, peer=a.fingerprint) as restarted:
        restarted.poll()
        assert restarted.show(sid)["status"] == "connected"
        a.poll()
        assert a.show(sid)["status"] == "connected"


def test_ciphertext_duplicate_preserves_single_task_claim(pair, tmp_path):
    import json

    a, b, sid, _ = pair
    selected = tmp_path / "data.txt"
    selected.write_text("A deterministic bounded task.")
    a.send_packet(sid, "selected", selected)
    b.poll()
    task = a.send_task(sid, "Digest")
    packet = a.relay.messages(b.fingerprint)[0].read_bytes()
    b.poll()
    b.approve(sid, task)
    b.execute_approved(sid)
    a.poll()
    a.relay.put(b.fingerprint, sid, packet)
    assert b.poll() == ["duplicate"]
    assert b.show(sid)["executions"] == 1
    # A real native commit with an unapplied authenticated application record
    # survives restart without widening the once-only task owner.
    with a.scope():
        a._send(sid, json.loads(a._row(sid)["task"]))
    path = a.relay.messages(b.fingerprint)[0]
    b.bridge.call("receive", wire=b64(path.read_bytes()), now=b.clock())
    b.close()
    with DropHome(b.home, a.relay.root, os.environ["VOOL_DROP_NODE"], peer=a.fingerprint) as restarted:
        assert restarted.poll() == ["duplicate"]
        assert restarted.show(sid)["executions"] == 1
