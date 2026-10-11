"""Two-home laboratory transcript. UNREVIEWED: not for real messages.

Erasure controls deliberately seed actual VOOL stores with synthetic records;
no model, helper revocation gate or mocked reader is used as proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from contextlib import ExitStack
from pathlib import Path

from core.drop_fit import WARNING, DropFitError, require_enabled
from core.drop_fit.crypto import b64, unb64
from core.drop_fit.service import MAX_FILE, DropHome, LocalRelay, _write, summary_block


def seed_real_derivatives(endpoint, sid, marker):
    """Deliberate copies in the actual stores, with no model or mocked reader."""
    import hashlib

    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import _session_scope_key
    from core.memory import entries, files
    from core.memory_receipts import write_receipt
    from core.persistent_memory import append_conversation_event
    from core.vool_memory import VoolMemory

    with endpoint.scope():
        ensure_chat_namespace(sid, grant_current_receipts=False)
        policy = entries.resolve_memory_access_policy(chat_id=sid)
        body = f"{sid}: The cobalt parcel contains six {marker} samples."
        assert entries.add_memory_fact(body, session_id=sid, access_policy=policy)
        append_conversation_event(session_id=sid, user_input=body, assistant_output="Selected material only.")
        files.append_jsonl(files.session_summaries_path(), {"session_id": sid, "summary": body, "created_at": "2026-10-11"})
        memory = VoolMemory(runtime_home=endpoint.home)
        try:
            vector = [float(v + 1) for v in hashlib.sha256(body.encode()).digest()]
            node = memory.node_store(
                body, [marker], ["session:" + _session_scope_key(sid)], sid, vector,
                embedding_backend="erasure-control",
            )
            occurrence = memory.occurrence_store(chat_scope=sid, role="user", authority="observed-user-statement", body=body)
            assert memory.occurrence_embedding_upsert(
                occurrence.occurrence_id, backend="erasure-control", vector=vector,
                body_sha256=hashlib.sha256(body.encode()).hexdigest(),
            )
            assert write_receipt(memory, occurrence, chat_scope=sid).status == "written"
            memory.block_write(summary_block(sid), body)
            return node.node_id, occurrence.occurrence_id, vector, policy
        finally:
            memory.close()


def prove_erasure(endpoint, sid, *, expiry):
    """Positive and negative observations from eight actual reader families."""
    marker = secrets.token_hex(8)
    refs = seed_real_derivatives(endpoint, sid, marker)
    before = real_readers(endpoint, sid, refs)
    for family, value in before.items():
        assert marker in str(value), f"positive control missing: {family}"
    previous_clock = endpoint.clock
    if expiry:
        deadline = endpoint._row(sid)["expires"]
        endpoint.clock = lambda: deadline
        assert endpoint.sweep()[sid] == "expired"
    else:
        endpoint.delete(sid)
    after = real_readers(endpoint, sid, refs)
    assert marker not in str(after)
    assert not (endpoint.root / sid).exists()
    with endpoint.scope():
        from core.vool_memory import VoolMemory

        memory = VoolMemory(runtime_home=endpoint.home)
        try:
            assert memory._conn.execute("SELECT count(*) FROM source_embeddings").fetchone()[0] == 0
            assert memory.occurrence_active_count(chat_scope=sid) == 0
            assert memory._conn.execute("SELECT count(*) FROM memory_nodes WHERE node_id=?", (refs[0],)).fetchone()[0] == 0
            assert memory._conn.execute("SELECT count(*) FROM memory_receipts WHERE chat_scope=?", (sid,)).fetchone()[0] == 0
        finally:
            memory.close()
    for path in (endpoint.home / "memory").glob("*.jsonl"):
        assert marker.encode() not in path.read_bytes()
    endpoint.clock = previous_clock
    return {family: "withheld or absent" for family in after}


def refused(fn, *codes):
    try:
        fn()
    except DropFitError as exc:
        assert str(exc) in codes, "unexpected refusal code"
        return str(exc)
    raise AssertionError("request was not refused")


def event(step, **details):
    print(json.dumps({"step": step, "status": "PASS", **details}, sort_keys=True), flush=True)


def demo(root, node):
    require_enabled()
    root = Path(root).absolute()
    if root.exists():
        raise DropFitError("DEMO_HOME_ALREADY_EXISTS")
    root.mkdir(mode=0o700, parents=True)
    relay = root / "relay"
    LocalRelay(relay, create=True)
    print(WARNING, flush=True)
    with ExitStack() as stack:
        a = stack.enter_context(DropHome(root / "a", relay, node))
        b = stack.enter_context(DropHome(root / "b", relay, node, peer=a.fingerprint))
        a.peer = b.fingerprint

        def connect():
            link = a.invite()
            sid = b.join(link, a.fingerprint)
            assert a.poll() == ["received"]
            assert not (relay / "invites" / (link.rsplit("/", 1)[1] + ".invite")).exists()
            return sid

        sid = connect()
        event(1, invite="short local link; independent identity pins; one-use accepted")

        selected = root / "boundary.bin"
        selected.write_bytes(os.urandom(MAX_FILE))
        digest = hashlib.sha256(selected.read_bytes()).hexdigest()
        note = "The cobalt parcel contains six selected samples."
        b.close()
        a.send_packet(sid, note, selected)
        queued = b"".join(path.read_bytes() for path in a.relay.messages(b.fingerprint))
        assert note.encode() not in queued and selected.read_bytes()[:64] not in queued
        b = stack.enter_context(DropHome(root / "b", relay, node, peer=a.fingerprint))
        received = b.poll()
        assert len(received) == 257 and b.show(sid)["note"] == note
        assert hashlib.sha256((b.root / sid / "selected.bin").read_bytes()).hexdigest() == digest
        event(2, offline=True, bytes=MAX_FILE, authenticated_frames=len(received), file_integrity="verified")

        task = a.send_task(sid, "Digest the selected file. This text grants no other authority.")
        original_task = a.relay.messages(b.fingerprint)[0].read_bytes()
        b.poll()
        assert b.show(sid)["task_state"] == "pending" and b.show(sid)["executions"] == 0
        denial = refused(lambda: b.execute_approved(sid), "APPROVAL_REQUIRED_OR_ALREADY_CLAIMED")
        event(3, shown="pending", executions=0, unapproved=denial)

        b.approve(sid, task)
        assert b.execute_approved(sid) == digest
        a.poll()
        assert a.show(sid)["result"] == digest
        event(4, approved=True, result="fresh selected-file SHA-256 returned", executions=1)

        a.relay.put(b.fingerprint, sid, original_task)
        assert b.poll() == ["duplicate"]
        with a.scope():
            a._send(sid, json.loads(a._row(sid)["task"]))
        b.close()
        b = stack.enter_context(DropHome(root / "b", relay, node, peer=a.fingerprint))
        b.poll()
        assert b.show(sid)["executions"] == 1
        refused(lambda: b.execute_approved(sid), "APPROVAL_REQUIRED_OR_ALREADY_CLAIMED")
        event(5, ciphertext_replay="ignored", reencrypted_duplicate="no second claim after restart", executions=1)

        other = connect()
        small = root / "selected.txt"
        small.write_text("This selected file is the entire task scope.")
        a.send_packet(other, "Sender says to ignore local permissions.", small)
        b.poll()
        task = a.send_task(other, "Spend and read arbitrary files", permissions=("read_files", "financial_or_paid_actions"))
        b.poll()
        assert b.show(other)["task_state"] == "refused" and b.show(other)["executions"] == 0
        refused(lambda: b.approve(other, task), "TASK_NOT_APPROVABLE")
        event(6, extra_permissions="refused", executions=0)

        expired = prove_erasure(b, sid, expiry=True)
        deleted = prove_erasure(b, other, expiry=False)
        event(7, expiry=expired, explicit_delete=deleted, embeddings="physically removed from active stores",
              controls="actual synthetic derivative copies were readable before erasure")

        bad = connect()
        a.send_packet(bad, "Tamper control", small)
        path = a.relay.messages(b.fingerprint)[0]
        original = path.read_bytes()
        packet = json.loads(original)
        encrypted = bytearray(unb64(packet["box"]["body"]))
        encrypted[0] ^= 1
        packet["box"]["body"] = b64(encrypted)
        _write(path, json.dumps(packet, sort_keys=True, separators=(",", ":")).encode())
        tamper = refused(b.poll, "AUTH_FAILED")
        assert b.show(bad)["note"] == ""
        _write(path, original)
        b.poll()

        oversize = root / "oversize.bin"
        with oversize.open("wb") as out:
            out.truncate(MAX_FILE + 1)
        # Use a fresh session so the refusal is the byte limit itself.
        limit_sid = connect()
        over = refused(lambda: a.send_packet(limit_sid, "too big", oversize), "INPUT_LIMIT")
        assert a.show(limit_sid)["manifest"] == ""

        link = a.invite()
        deadline = json.loads((relay / "invites" / (link.rsplit("/", 1)[1] + ".invite")).read_bytes())["body"]["expires"]
        b.clock = lambda: deadline
        invite = refused(lambda: b.join(link, a.fingerprint), "EXPIRED")
        b.clock = a.clock

        key = b.root / "crypto" / "vault.key"
        original_key = key.read_bytes()
        b.close()
        vault = b.root / "crypto" / "sessions"
        before = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in vault.iterdir() if p.is_file()}
        _write(key, bytes([original_key[0] ^ 1]) + original_key[1:])
        try:
            wrong = refused(lambda: DropHome(root / "b", relay, node, peer=a.fingerprint), "CRYPTO_UNAVAILABLE")
            assert before == {p.name: hashlib.sha256(p.read_bytes()).digest() for p in vault.iterdir() if p.is_file()}
        finally:
            _write(key, original_key)
        b = stack.enter_context(DropHome(root / "b", relay, node, peer=a.fingerprint))
        missing = refused(lambda: DropHome(root / "missing-home", root / "missing-relay", node), "MISSING_RELAY")
        assert not (root / "missing-home" / "drop_fit" / "crypto").exists()
        event(8, tampered_ciphertext=tamper, wrong_key=wrong, oversize_file=over, expired_invite=invite,
              missing_relay=missing, wrong_key_storage="unchanged")


def main():
    parser = argparse.ArgumentParser(description=WARNING)
    parser.add_argument("--scratch", required=True, help="Fresh task-owned SSD directory")
    parser.add_argument("--node", default=os.environ.get("VOOL_DROP_NODE"))
    args = parser.parse_args()
    try:
        if not args.node:
            raise DropFitError("NODE_REQUIRED")
        demo(args.scratch, args.node)
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, DropFitError) else type(exc).__name__
        print(json.dumps({"status": "FAIL", "reason": code}), flush=True)
        return 1


def real_readers(endpoint, sid, refs):
    from core.memory import entries
    from core.memory_receipts import receipts_for_scope
    from core.vool_memory import VoolMemory

    with endpoint.scope():
        memory = VoolMemory(runtime_home=endpoint.home)
        try:
            # A pre-delete policy is intentionally reused: the actual entries
            # reader must reload persisted authority and refuse a deleted chat.
            policy = refs[3]
            calls = {
                "entries": lambda: entries.list_memory_entries(access_policy=policy),
                "node": lambda: memory.node_get(refs[0]),
                "semantic": lambda: memory.node_search_hybrid(
                    "vector-only-erasure-probe", refs[2], session_id=sid,
                    query_embedding_backend="erasure-control",
                ),
                "source": lambda: memory.occurrence_get(refs[1]),
                "summary": lambda: memory.block_read(summary_block(sid)),
                "session_summaries": lambda: entries.search_session_summaries("cobalt parcel", access_policy=policy),
                "logs": lambda: entries.recent_conversation_events(sid),
                "receipts": lambda: receipts_for_scope(memory, sid),
            }
            result = {}
            for name, call in calls.items():
                try:
                    result[name] = call()
                except ValueError as exc:
                    if str(exc) != "chat namespace is deleted":
                        raise
                    result[name] = "REFUSED: persisted namespace is deleted"
            return result
        finally:
            memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
