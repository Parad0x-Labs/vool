"""A queued dispatch that carries no turn id consumes its own claim instead of refusing itself.

API-shaped callers (the price-wait queue's own dispatch, scripts) post the claimed item's
queue_item_id without a turn_id. The claim holds the chat's one slot for exactly that dispatch,
so the dispatch must run; any other caller is still refused while the claim holds.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.live_turns import register_turn, unregister_turn
from core.runtime_continuity import (
    admit_chat_turn,
    admitted_chat_turn_token,
    chat_admission_free_for,
    chat_turn_active,
    claim_next_message,
    complete_queue_item,
    configure_runtime_continuity_db_path,
    enqueue_message,
    get_queue_item,
    release_chat_turn_admission,
    release_claimed_queue_item,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations


class TokenlessQueuedDispatchTests(unittest.TestCase):
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

    def _claimed(self, sid: str) -> str:
        item = enqueue_message(session_id=sid, payload={"text": "queued"})
        claimed = claim_next_message(sid, lease_owner="api")
        self.assertEqual(claimed["queue_item_id"], item["queue_item_id"])
        self.assertTrue(chat_turn_active(sid))
        return item["queue_item_id"]

    def test_its_own_claim_is_consumed_and_the_slot_is_free_after(self) -> None:
        sid = "openclaw:tokenless-own"
        qid = self._claimed(sid)
        self.assertTrue(chat_admission_free_for(sid, queue_item_id=qid))
        admitted = admit_chat_turn(sid, "", queue_item_id=qid)
        self.assertEqual(admitted, {"admitted": True, "reason": "claim_consumed_unreserved"})
        self.assertEqual(admitted_chat_turn_token(sid), "")
        # The queue's terminal op releasing by the consumed claim's id is a harmless no-op.
        self.assertFalse(release_chat_turn_admission(sid, qid))
        # The same claim cannot be presented twice.
        claim_again = enqueue_message(session_id=sid, payload={"text": "next"})
        claim_next_message(sid, lease_owner="api")
        self.assertFalse(admit_chat_turn(sid, "", queue_item_id=qid)["admitted"])
        self.assertTrue(admit_chat_turn(sid, "", queue_item_id=claim_again["queue_item_id"])["admitted"])

    def test_a_late_release_cannot_requeue_the_running_item(self) -> None:
        sid = "openclaw:tokenless-release"
        qid = self._claimed(sid)
        self.assertTrue(admit_chat_turn(sid, "", queue_item_id=qid)["admitted"])
        self.assertIsNone(release_claimed_queue_item(qid, session_id=sid))
        self.assertEqual(get_queue_item(qid)["status"], "in_flight")
        self.assertIsNone(claim_next_message(sid, lease_owner="api"))  # it never runs twice
        complete_queue_item(qid, status="completed")
        self.assertEqual(get_queue_item(qid)["status"], "completed")

    def test_another_id_or_none_is_refused_while_the_claim_holds(self) -> None:
        sid = "openclaw:tokenless-other"
        qid = self._claimed(sid)
        self.assertEqual(admit_chat_turn(sid, "", queue_item_id="msg-not-mine")["admitted"], False)
        self.assertEqual(admit_chat_turn(sid, "", queue_item_id="")["admitted"], False)
        self.assertEqual(admitted_chat_turn_token(sid), qid)

    def test_a_running_worker_refuses_even_the_claims_own_dispatch(self) -> None:
        sid = "openclaw:tokenless-live"
        qid = self._claimed(sid)
        register_turn(sid, "t-live")
        try:
            self.assertEqual(admit_chat_turn(sid, "", queue_item_id=qid), {"admitted": False, "reason": "turn_active"})
            self.assertEqual(admitted_chat_turn_token(sid), qid)
        finally:
            unregister_turn(sid, "t-live")

    def test_no_claim_no_token_runs_unreserved(self) -> None:
        sid = "openclaw:tokenless-free"
        self.assertEqual(admit_chat_turn(sid, ""), {"admitted": True, "reason": "unreserved"})
        self.assertFalse(chat_turn_active(sid))


if __name__ == "__main__":
    unittest.main()
