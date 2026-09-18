"""Persistent per-session message queue (Checkpoint 6).

Covers the correctness-critical requirements from the queue acceptance/adversarial specs:
idempotent enqueue (no double-submit), atomic claim (exactly-once dispatch under concurrency),
per-session scoping + ordering, cancel-only-pending, and restart recovery.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from core.runtime_continuity import (
    cancel_queue_item,
    claim_next_message,
    complete_queue_item,
    configure_runtime_continuity_db_path,
    enqueue_message,
    get_queue_item,
    list_queued_messages,
    recover_stale_queue_items,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations


class MessageQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db = Path(self._tmp.name) / "queue.db"
        run_migrations(db_path=db)
        configure_runtime_continuity_db_path(str(db))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_enqueue_orders_and_scopes_per_session(self) -> None:
        a1 = enqueue_message(session_id="s:A", payload={"text": "one"})
        a2 = enqueue_message(session_id="s:A", payload={"text": "two"})
        b1 = enqueue_message(session_id="s:B", payload={"text": "other"})
        self.assertEqual(a1["seq"], 1)
        self.assertEqual(a2["seq"], 2)
        self.assertEqual(b1["seq"], 1)  # per-session seq
        a_items = list_queued_messages("s:A")
        self.assertEqual([i["payload"]["text"] for i in a_items], ["one", "two"])
        # Session B's queue is isolated.
        self.assertEqual([i["payload"]["text"] for i in list_queued_messages("s:B")], ["other"])

    def test_enqueue_is_idempotent_per_idempotency_key(self) -> None:
        first = enqueue_message(session_id="s:A", payload={"text": "hi"}, idempotency_key="k-1")
        second = enqueue_message(session_id="s:A", payload={"text": "hi"}, idempotency_key="k-1")
        self.assertEqual(first["queue_item_id"], second["queue_item_id"])
        self.assertEqual(len(list_queued_messages("s:A")), 1)  # no duplicate row

    def test_claim_returns_oldest_then_next_then_none(self) -> None:
        enqueue_message(session_id="s:A", payload={"text": "one"})
        enqueue_message(session_id="s:A", payload={"text": "two"})
        c1 = claim_next_message("s:A", lease_owner="w1")
        c2 = claim_next_message("s:A", lease_owner="w1")
        c3 = claim_next_message("s:A", lease_owner="w1")
        self.assertEqual(c1["payload"]["text"], "one")
        self.assertEqual(c1["status"], "in_flight")
        self.assertEqual(c2["payload"]["text"], "two")
        self.assertIsNone(c3)  # nothing left pending

    def test_atomic_claim_dispatches_each_item_exactly_once_under_concurrency(self) -> None:
        # 40 messages, 12 racing claimers -> every item claimed exactly once, no duplicates.
        total = 40
        for i in range(total):
            enqueue_message(session_id="s:C", payload={"text": f"m{i}"})
        claimed: list[str] = []
        claimed_lock = threading.Lock()
        barrier = threading.Barrier(12)

        def worker() -> None:
            barrier.wait()
            while True:
                item = claim_next_message("s:C", lease_owner="race")
                if item is None:
                    return
                with claimed_lock:
                    claimed.append(item["queue_item_id"])

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(claimed), total)
        self.assertEqual(len(set(claimed)), total)  # exactly once each
        self.assertEqual(list_queued_messages("s:C", statuses=("pending",)), [])

    def test_cancel_only_pending_items(self) -> None:
        item = enqueue_message(session_id="s:A", payload={"text": "cancel me"})
        self.assertTrue(cancel_queue_item(item["queue_item_id"], session_id="s:A"))
        self.assertEqual(get_queue_item(item["queue_item_id"])["status"], "cancelled")
        # A cancelled item is not pending and cannot be claimed.
        self.assertIsNone(claim_next_message("s:A", lease_owner="w"))

    def test_in_flight_item_cannot_be_cancelled(self) -> None:
        enqueue_message(session_id="s:A", payload={"text": "running"})
        claimed = claim_next_message("s:A", lease_owner="w")
        self.assertFalse(cancel_queue_item(claimed["queue_item_id"], session_id="s:A"))
        self.assertEqual(get_queue_item(claimed["queue_item_id"])["status"], "in_flight")

    def test_cancel_is_session_scoped(self) -> None:
        item = enqueue_message(session_id="s:A", payload={"text": "x"})
        # Wrong session cannot cancel it.
        self.assertFalse(cancel_queue_item(item["queue_item_id"], session_id="s:B"))
        self.assertEqual(get_queue_item(item["queue_item_id"])["status"], "pending")

    def test_complete_transitions_and_removes_from_active_list(self) -> None:
        item = enqueue_message(session_id="s:A", payload={"text": "done"})
        claim_next_message("s:A", lease_owner="w")
        complete_queue_item(item["queue_item_id"], status="completed")
        self.assertEqual(get_queue_item(item["queue_item_id"])["status"], "completed")
        self.assertEqual(list_queued_messages("s:A"), [])

    def test_restart_recovery_resets_in_flight_to_pending(self) -> None:
        # An interrupted turn leaves an in_flight item; startup recovery re-queues it so it is
        # not lost, and a clean reconnect (pending untouched) does not double-run anything.
        enqueue_message(session_id="s:A", payload={"text": "one"})
        enqueue_message(session_id="s:A", payload={"text": "two"})
        c1 = claim_next_message("s:A", lease_owner="w")  # -> in_flight (interrupted mid-turn)
        reset = recover_stale_queue_items()
        self.assertEqual(reset, 1)
        self.assertEqual(get_queue_item(c1["queue_item_id"])["status"], "pending")
        # Both are pending again, in original order.
        self.assertEqual([i["payload"]["text"] for i in list_queued_messages("s:A", statuses=("pending",))], ["one", "two"])

    def test_queued_text_is_secret_scrubbed_at_rest(self) -> None:
        secret = "sk-or-v1-abcdefghijklmnop0123456789abcdef"
        item = enqueue_message(session_id="s:A", payload={"text": f"use my key {secret}"})
        self.assertNotIn(secret, item["payload"]["text"])


class QueueEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db = Path(self._tmp.name) / "queue.db"
        run_migrations(db_path=db)
        configure_runtime_continuity_db_path(str(db))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    @staticmethod
    def _parse(resp: object) -> dict:
        import json

        raw = getattr(resp, "body", b"")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        return json.loads(raw) if raw else {}

    def _post(self, body: dict) -> object:
        from types import SimpleNamespace

        from core.web.api import service

        return service.dispatch_post(
            path="/api/chat/queue", body=body, headers={}, client_host="127.0.0.1", runtime=SimpleNamespace(runtime_version_stamp={}),
            model_name="vool", workspace_root_provider=lambda: ".",
        )

    def _get(self, session: str) -> object:
        from types import SimpleNamespace

        from core.web.api import service

        return service.dispatch_get(
            path="/api/chat/queue", query={"session": [session]}, client_host="127.0.0.1",
            runtime=SimpleNamespace(runtime_version_stamp={}), model_name="vool",
        )

    def test_enqueue_list_claim_cancel_endpoint_flow(self) -> None:
        sid = "openclaw:queue-endpoint"
        r1 = self._post({"op": "enqueue", "session_id": sid, "text": "first"})
        self.assertEqual(r1.status, 200)
        self.assertEqual(self._parse(r1)["position"], 1)
        r2 = self._post({"op": "enqueue", "session_id": sid, "text": "second"})
        self.assertEqual(self._parse(r2)["position"], 2)
        listed = self._parse(self._get(sid))
        self.assertEqual([i["payload"]["text"] for i in listed["queue"]], ["first", "second"])
        # Idempotent enqueue via the endpoint.
        again = self._parse(self._post({"op": "enqueue", "session_id": sid, "text": "first", "idempotency_key": "k9"}))
        again2 = self._parse(self._post({"op": "enqueue", "session_id": sid, "text": "first", "idempotency_key": "k9"}))
        self.assertEqual(again["item"]["queue_item_id"], again2["item"]["queue_item_id"])
        # Claim dispatches the oldest.
        claim = self._parse(self._post({"op": "claim", "session_id": sid}))
        self.assertEqual(claim["item"]["payload"]["text"], "first")
        # Cancel a still-pending one.
        pending_id = self._parse(self._get(sid))["queue"][-1]["queue_item_id"]  # last pending
        cancel = self._parse(self._post({"op": "cancel", "session_id": sid, "queue_item_id": pending_id}))
        self.assertTrue(cancel["cancelled"])

    def test_endpoint_rejects_bad_input(self) -> None:
        self.assertEqual(self._post({"op": "enqueue", "session_id": "", "text": "x"}).status, 400)
        self.assertEqual(self._post({"op": "enqueue", "session_id": "s", "text": ""}).status, 400)
        self.assertEqual(self._post({"op": "bogus", "session_id": "s"}).status, 400)
        self.assertEqual(self._post({"op": "complete", "session_id": "s"}).status, 400)


if __name__ == "__main__":
    unittest.main()
