from __future__ import annotations

import contextlib
import importlib
import json
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import network.protocol as protocol_mod
import network.signer as signer_mod
from storage.db import get_connection
from storage.migrations import run_migrations


class ProtocolRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        importlib.reload(signer_mod)
        importlib.reload(protocol_mod)
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM nonce_cache")
            for table in ("identity_revocations", "identity_key_history"):
                with contextlib.suppress(Exception):
                    conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    def test_credit_offer_requires_valid_schema(self) -> None:
        raw = protocol_mod.encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="CREDIT_OFFER",
            sender_peer_id=signer_mod.get_local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={},
        )
        with self.assertRaises(ValueError):
            protocol_mod.Protocol.decode_and_validate(raw)

    def test_store_nonce_prunes_old_entries(self) -> None:
        stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO nonce_cache (sender_peer_id, nonce, seen_at) VALUES (?, ?, ?)",
                ("peer-stale", "nonce-stale", stale_ts),
            )
            conn.commit()
        finally:
            conn.close()

        protocol_mod.store_nonce("peer-fresh", "nonce-fresh")

        conn = get_connection()
        try:
            rows = conn.execute("SELECT sender_peer_id, nonce FROM nonce_cache ORDER BY sender_peer_id ASC").fetchall()
        finally:
            conn.close()

        serialized = [json.dumps(dict(row), sort_keys=True) for row in rows]
        self.assertFalse(any("peer-stale" in row for row in serialized))
        self.assertTrue(any("peer-fresh" in row for row in serialized))

    def test_report_abuse_requires_valid_schema(self) -> None:
        original_verify = protocol_mod.verify_signature
        protocol_mod.verify_signature = lambda envelope: True
        self.addCleanup(lambda: setattr(protocol_mod, "verify_signature", original_verify))

        bad = protocol_mod.encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="REPORT_ABUSE",
            sender_peer_id=signer_mod.get_local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={"report_id": "short"},
        )
        with self.assertRaises(ValueError):
            protocol_mod.Protocol.decode_and_validate(bad)

        good = protocol_mod.encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="REPORT_ABUSE",
            sender_peer_id=signer_mod.get_local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={
                "report_id": f"report-{uuid.uuid4().hex}",
                "accused_peer_id": f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                "signal_type": "spam_wave",
                "severity": 0.9,
                "task_id": str(uuid.uuid4()),
                "details": {"source": "test"},
                "ttl": 2,
            },
        )
        envelope = protocol_mod.Protocol.decode_and_validate(good)
        self.assertEqual(envelope["msg_type"], "REPORT_ABUSE")

    def test_shard_payload_requires_valid_schema(self) -> None:
        bad = protocol_mod.encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="SHARD_PAYLOAD",
            sender_peer_id=signer_mod.get_local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={"query_id": "short", "shard": {"shard_id": "x"}},
        )
        with self.assertRaises(ValueError):
            protocol_mod.Protocol.decode_and_validate(bad)

    def test_nonce_consume_is_atomic_under_race(self) -> None:
        raw = protocol_mod.encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="PING",
            sender_peer_id=signer_mod.get_local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={},
        )
        successes = 0
        failures = 0
        lock = threading.Lock()
        worker_count = 8
        barrier = threading.Barrier(worker_count)

        def worker() -> None:
            nonlocal successes, failures
            try:
                barrier.wait(timeout=10.0)
            except Exception:
                return
            try:
                protocol_mod.Protocol.decode_and_validate(raw)
                with lock:
                    successes += 1
            except Exception:
                with lock:
                    failures += 1

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)

        self.assertEqual(successes, 1)
        self.assertEqual(successes + failures, worker_count)


if __name__ == "__main__":
    unittest.main()
