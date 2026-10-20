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
    admit_chat_turn,
    admitted_chat_turn_token,
    cancel_queue_item,
    chat_turn_active,
    claim_next_message,
    complete_queue_item,
    configure_runtime_continuity_db_path,
    enqueue_message,
    get_queue_item,
    list_queued_messages,
    recover_stale_queue_items,
    release_chat_turn_admission,
    release_claimed_queue_item,
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

    def test_one_outstanding_claim_per_session_then_next_after_completion(self) -> None:
        """Updated to the now-explicit per-session admission law (frozen review
        artifacts/git-queue-review-20260930): the oldest item is claimed and the SESSION is
        reserved until that claim completes -- a second claim in the claim-to-start window is
        refused rather than handing a second in_flight row to another tab. The original
        oldest-then-next order and the terminal empty answer are unchanged."""
        enqueue_message(session_id="s:A", payload={"text": "one"})
        enqueue_message(session_id="s:A", payload={"text": "two"})
        c1 = claim_next_message("s:A", lease_owner="w1")
        c2 = claim_next_message("s:A", lease_owner="w2")  # the claim-to-start window: refused
        self.assertEqual(c1["payload"]["text"], "one")
        self.assertEqual(c1["status"], "in_flight")
        self.assertIsNone(c2)
        enqueue_message(session_id="s:B", payload={"text": "side"})
        self.assertIsNotNone(claim_next_message("s:B", lease_owner="w3"))  # another chat is unaffected
        complete_queue_item(c1["queue_item_id"])  # the claim's turn ended: the slot frees
        c3 = claim_next_message("s:A", lease_owner="w1")
        self.assertEqual(c3["payload"]["text"], "two")
        complete_queue_item(c3["queue_item_id"])
        self.assertIsNone(claim_next_message("s:A", lease_owner="w1"))  # nothing left pending

    def test_atomic_claim_dispatches_each_item_exactly_once_under_concurrency(self) -> None:
        """Reworked for the per-session admission law without giving up its exactly-once
        proof. Phase 1 keeps the original single-row contention: many racing claimers, ONE
        item, exactly one winner. Phase 2 restores the many-items coverage the old test had:
        racing claimer loops that complete each claim before claiming again -- every item is
        dispatched exactly once, in FIFO batches, with no duplicate claim of any row."""
        # Phase 1: one item, 12 racing claimers -> exactly one winner (CAS exactly-once).
        enqueue_message(session_id="s:RACE", payload={"text": "the one"})
        winners: list[str] = []
        winners_lock = threading.Lock()
        barrier = threading.Barrier(12)

        def contender() -> None:
            barrier.wait()
            item = claim_next_message("s:RACE", lease_owner="race")
            if item is not None:
                with winners_lock:
                    winners.append(item["queue_item_id"])

        threads = [threading.Thread(target=contender) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(winners), 1)
        complete_queue_item(winners[0])

        # Phase 2: 40 items, 12 racing claim/complete loops -> every item exactly once.
        total = 40
        for i in range(total):
            enqueue_message(session_id="s:C", payload={"text": f"m{i}"})
        claimed: list[str] = []
        claimed_lock = threading.Lock()
        barrier2 = threading.Barrier(12)

        def worker() -> None:
            barrier2.wait()
            while True:
                item = claim_next_message("s:C", lease_owner="race")
                if item is None:
                    if not list_queued_messages("s:C", statuses=("pending",)):
                        return  # genuinely drained (a None can also mean the slot is taken)
                    continue
                with claimed_lock:
                    claimed.append(item["queue_item_id"])
                complete_queue_item(item["queue_item_id"])

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
        self.assertEqual(
            [i["payload"]["text"] for i in list_queued_messages("s:A", statuses=("pending",))], ["one", "two"]
        )

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
            path="/api/chat/queue",
            body=body,
            headers={},
            client_host="127.0.0.1",
            runtime=SimpleNamespace(runtime_version_stamp={}),
            model_name="vool",
            workspace_root_provider=lambda: ".",
        )

    def _get(self, session: str) -> object:
        from types import SimpleNamespace

        from core.web.api import service

        return service.dispatch_get(
            path="/api/chat/queue",
            query={"session": [session]},
            client_host="127.0.0.1",
            runtime=SimpleNamespace(runtime_version_stamp={}),
            model_name="vool",
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

    def test_claim_refuses_while_a_turn_is_live_and_resumes_after(self) -> None:
        """The one-slot-per-chat law at the queue's own door: while a turn worker is live for a
        session, a claim answers empty with `turn_active` (the page exits its pump instead of
        spinning), the item stays pending, another session's slot is untouched, and the claim
        proceeds the moment the turn ends."""
        from core.live_turns import register_turn, unregister_turn
        from core.runtime_continuity import chat_turn_active

        sid = "openclaw:slot-law"
        enqueue_message(session_id=sid, payload={"text": "held"})
        enqueue_message(session_id="s:other", payload={"text": "free"})
        register_turn(sid, "t-slot-1")
        try:
            self.assertTrue(chat_turn_active(sid))
            self.assertIsNone(claim_next_message(sid))  # the authority itself refuses
            refused = self._parse(self._post({"op": "claim", "session_id": sid}))
            self.assertIsNone(refused["item"])
            self.assertTrue(refused["turn_active"])
            self.assertEqual(list_queued_messages(sid)[0]["status"], "pending")  # untouched
            self.assertTrue(self._parse(self._get(sid))["turn_active"])  # the read agrees
            # A different chat's slot is independent.
            other = self._parse(self._post({"op": "claim", "session_id": "s:other"}))
            self.assertEqual(other["item"]["payload"]["text"], "free")
            self.assertFalse(other.get("turn_active", False))
        finally:
            unregister_turn(sid, "t-slot-1")
        self.assertFalse(chat_turn_active(sid))
        claim = self._parse(self._post({"op": "claim", "session_id": sid}))
        self.assertEqual(claim["item"]["payload"]["text"], "held")
        self.assertFalse(claim.get("turn_active", False))

    def test_admission_reserves_claim_to_start_and_consumes_its_own_claim(self) -> None:
        """The frozen review's first failure, as the queue's own law: a claim reserves the
        session for the claim-to-start window; a second claim is refused; the dispatch POST
        consumes its OWN claim by presenting the claim's queue_item_id; presenting any other
        id cannot steal the reservation; the worker's end releases it."""
        sid = "openclaw:admission-law"
        first = enqueue_message(session_id=sid, payload={"text": "first"})
        enqueue_message(session_id=sid, payload={"text": "second"})
        claimed = claim_next_message(sid, lease_owner="tab-a")
        self.assertEqual(claimed["queue_item_id"], first["queue_item_id"])
        self.assertTrue(chat_turn_active(sid))  # the claim-to-start window holds the slot
        self.assertEqual(admitted_chat_turn_token(sid), first["queue_item_id"])
        self.assertIsNone(claim_next_message(sid, lease_owner="tab-b"))  # no second in_flight row

        # A dispatch presenting SOMEONE ELSE'S (or a stale) queue_item_id is refused.
        self.assertFalse(admit_chat_turn(sid, "turn-impostor", queue_item_id="msg-not-mine")["admitted"])
        self.assertFalse(admit_chat_turn(sid, "turn-impostor", queue_item_id="")["admitted"])
        # The dispatch presenting its own claim consumes the reservation.
        consumed = admit_chat_turn(sid, "turn-real", queue_item_id=first["queue_item_id"])
        self.assertTrue(consumed["admitted"])
        self.assertEqual(consumed["reason"], "claim_consumed")
        self.assertEqual(admitted_chat_turn_token(sid), "turn-real")

        # The worker's end releases; the session is free again.
        self.assertTrue(release_chat_turn_admission(sid, "turn-real"))
        self.assertFalse(chat_turn_active(sid))
        nxt = claim_next_message(sid, lease_owner="tab-b")
        self.assertEqual(nxt["payload"]["text"], "second")

    def test_abandoned_claim_admission_is_lease_bounded(self) -> None:
        """A never-started claim must not hold the chat forever: the reservation lapses with
        its lease, and the interrupted row remains startup recovery's -- the chat itself is
        free for new work."""
        sid = "openclaw:abandoned-claim"
        item = enqueue_message(session_id=sid, payload={"text": "stranded"})
        claimed = claim_next_message(sid, lease_owner="gone")
        self.assertIsNotNone(claimed)
        self.assertTrue(chat_turn_active(sid))
        try:
            from core import runtime_continuity as rc

            original = rc._admission_lease_seconds()
            rc._ADMITTED_TURNS[sid]["created"] -= original + 1  # age past the lease
            self.assertFalse(chat_turn_active(sid))
            nxt = enqueue_message(session_id=sid, payload={"text": "fresh"})
            admitted = admit_chat_turn(sid, "turn-after-abandon")
            self.assertTrue(admitted["admitted"])  # the chat is usable again
        finally:
            pass
        # The interrupted row is untouched: recovery's owner, not the lease's.
        self.assertEqual(get_queue_item(item["queue_item_id"])["status"], "in_flight")

    def test_release_returns_the_original_claim_ownership_checked(self) -> None:
        """The frozen review's second failure, as the queue's own law: a claimed message
        whose turn is refused returns to PENDING as the SAME row in the SAME FIFO position --
        no duplicate at the tail, no foreign release, and once a dispatch has consumed the
        claim the item belongs to that turn."""
        sid = "openclaw:release-law"
        m2 = enqueue_message(session_id=sid, payload={"text": "keep-me"}, idempotency_key="orig-1")
        m3 = enqueue_message(session_id=sid, payload={"text": "behind"})
        claimed = claim_next_message(sid, lease_owner="tab-b")
        self.assertEqual(claimed["queue_item_id"], m2["queue_item_id"])

        # A foreign session cannot release it; a pending row is not releasable either.
        self.assertIsNone(release_claimed_queue_item(m2["queue_item_id"], session_id="openclaw:other"))
        self.assertIsNone(release_claimed_queue_item(m3["queue_item_id"], session_id=sid))

        released = release_claimed_queue_item(m2["queue_item_id"], session_id=sid)
        self.assertIsNotNone(released)
        self.assertEqual(released["queue_item_id"], m2["queue_item_id"])  # same identity
        self.assertEqual(released["seq"], m2["seq"])  # same FIFO position
        self.assertEqual(released["status"], "pending")
        self.assertEqual(released.get("idempotency_key"), "orig-1")  # idempotency preserved
        rows = list_queued_messages(sid)
        self.assertEqual([r["payload"]["text"] for r in rows], ["keep-me", "behind"])  # no copy, no reorder
        self.assertFalse(chat_turn_active(sid))  # the claim's reservation went with it

        # Once a dispatch has consumed a claim, a late release cannot take the item back.
        claimed_again = claim_next_message(sid, lease_owner="tab-b")
        self.assertTrue(admit_chat_turn(sid, "turn-owner", queue_item_id=claimed_again["queue_item_id"])["admitted"])
        self.assertIsNone(release_claimed_queue_item(claimed_again["queue_item_id"], session_id=sid))

    def test_endpoint_release_op_is_ownership_checked(self) -> None:
        sid = "openclaw:release-endpoint"
        enqueue_message(session_id=sid, payload={"text": "claimed"})
        claimed = self._parse(self._post({"op": "claim", "session_id": sid}))["item"]
        refused = self._parse(
            self._post({"op": "release", "session_id": "openclaw:elsewhere", "queue_item_id": claimed["queue_item_id"]})
        )
        self.assertFalse(refused["released"])
        ok = self._parse(self._post({"op": "release", "session_id": sid, "queue_item_id": claimed["queue_item_id"]}))
        self.assertTrue(ok["released"])
        self.assertEqual(ok["item"]["status"], "pending")
        self.assertEqual(self._post({"op": "release", "session_id": sid}).status, 400)

    def test_endpoint_rejects_bad_input(self) -> None:
        self.assertEqual(self._post({"op": "enqueue", "session_id": "", "text": "x"}).status, 400)
        self.assertEqual(self._post({"op": "enqueue", "session_id": "s", "text": ""}).status, 400)
        self.assertEqual(self._post({"op": "bogus", "session_id": "s"}).status, 400)
        self.assertEqual(self._post({"op": "complete", "session_id": "s"}).status, 400)


if __name__ == "__main__":
    unittest.main()
