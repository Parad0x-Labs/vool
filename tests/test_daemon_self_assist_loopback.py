from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.daemon.messages import on_message
from network import rate_limiter
from network.assist_router import build_task_claim_message, handle_incoming_assist_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations
from tests.task_offer_fixtures import build_signed_task_offer_message


class _FakeDaemon:
    def __init__(self) -> None:
        self.local_capability_ad = None
        self.config = SimpleNamespace(local_host_group_hint_hash="hostgroup-test-hash")
        self.udp_sends: list[tuple[str, int, str]] = []
        self.spawned_workers: list[str] = []

    def _refresh_assist_status(self) -> None:
        return None

    def _idle_assist_config(self) -> None:
        return None

    def _active_assignment_count(self) -> int:
        return 0

    def _send_or_log(self, host: str, port: int, raw: bytes, *, message_type: str, target_id: str) -> bool:
        self.udp_sends.append((host, int(port), message_type))
        return True

    def _spawn_limited_worker(self, *, target, args, name: str, target_id: str) -> None:
        self.spawned_workers.append(name)

    def _maybe_execute_local_assignment_from_raw(self, raw: bytes, addr: tuple[str, int]) -> None:
        return None

    def _maybe_auto_review_result_from_raw(self, raw: bytes, addr: tuple[str, int]) -> None:
        return None


class DaemonSelfAssistLoopbackTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        rate_limiter.reset_peer(local_peer_id())
        conn = get_connection()
        try:
            conn.execute("DELETE FROM task_assignments")
            conn.execute("DELETE FROM task_claims")
            conn.execute("DELETE FROM task_capsules")
            conn.execute("DELETE FROM task_offers")
            conn.commit()
        finally:
            conn.close()

    def test_self_claim_assignment_is_processed_in_process(self) -> None:
        raw_offer, offer, _capsule = build_signed_task_offer_message()
        result = handle_incoming_assist_message(raw_bytes=raw_offer, source_addr=None)
        self.assertTrue(result.ok, result.reason)

        claim_raw = build_task_claim_message(
            task_id=offer.task_id,
            declared_capabilities=["research"],
            current_load=0,
        )
        daemon = _FakeDaemon()

        # The claim datagram arrives from an ephemeral send socket that is
        # already closed; replying there would drop the TASK_ASSIGN.
        on_message(daemon, claim_raw, ("127.0.0.1", 59999))

        conn = get_connection()
        try:
            assignment_row = conn.execute(
                "SELECT * FROM task_assignments WHERE task_id = ?", (offer.task_id,)
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(assignment_row)
        self.assertEqual(assignment_row["helper_peer_id"], local_peer_id())
        # The generated TASK_ASSIGN was handled in-process, not sent over UDP.
        self.assertEqual(daemon.udp_sends, [])
        self.assertEqual(daemon.spawned_workers, ["vool-local-assignment"])


if __name__ == "__main__":
    unittest.main()
