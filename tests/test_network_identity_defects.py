from __future__ import annotations

import threading
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

from core.daemon import tasks as daemon_tasks
from core.discovery_index import peer_trust, register_capability_ad
from network import rate_limiter
from network.assist_models import CapabilityAd
from network.assist_router import handle_incoming_assist_message
from network.pow_hashcash import generate_pow
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clear_capability_tables() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM agent_capabilities")
        conn.execute("DELETE FROM peers")
        conn.commit()
    finally:
        conn.close()


def _capability_ad_payload(*, agent_id: str, trust_score: float, nonce: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "status": "idle",
        "capabilities": ["code"],
        "capacity": 4,
        "trust_score": trust_score,
        "pow_difficulty": 1,
        "timestamp": _now(),
        "genesis_nonce": nonce,
    }


class CapabilityAdIdentityTests(unittest.TestCase):
    """Defect (1): CAPABILITY_AD trust spoofing / missing agent_id binding."""

    def setUp(self) -> None:
        run_migrations()
        _clear_capability_tables()
        rate_limiter.reset_peer(local_peer_id())
        # Keep PoW cheap for the test.
        self._env = mock.patch.dict("os.environ", {"VOOL_POW_DIFFICULTY": "1"})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def _routing_trust(self, peer_id: str) -> float | None:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT trust_score FROM agent_capabilities WHERE peer_id = ?",
                (peer_id,),
            ).fetchone()
            return float(row["trust_score"]) if row else None
        finally:
            conn.close()

    def _self_reported_trust(self, peer_id: str) -> float | None:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT self_reported_trust FROM agent_capabilities WHERE peer_id = ?",
                (peer_id,),
            ).fetchone()
            return float(row["self_reported_trust"]) if row else None
        finally:
            conn.close()

    def test_capability_ad_with_mismatched_agent_id_is_rejected(self) -> None:
        victim = "victim-agent-id-0000000000"
        # Sender is the local (signing) peer, but the ad claims a different agent.
        nonce = generate_pow(victim, target_difficulty=1)
        payload = _capability_ad_payload(agent_id=victim, trust_score=0.0, nonce=nonce)
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="CAPABILITY_AD",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload=payload,
        )

        result = handle_incoming_assist_message(raw_bytes=raw, source_addr=None)

        self.assertFalse(result.ok)
        self.assertIn("agent_id", result.reason)
        # Defamation attempt must not have written any trust row for the victim.
        self.assertIsNone(self._routing_trust(victim))

    def test_self_declared_high_trust_does_not_reach_routing_column(self) -> None:
        sender = local_peer_id()
        nonce = generate_pow(sender, target_difficulty=1)
        payload = _capability_ad_payload(agent_id=sender, trust_score=1.0, nonce=nonce)
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="CAPABILITY_AD",
            sender_peer_id=sender,
            nonce=uuid.uuid4().hex,
            payload=payload,
        )

        result = handle_incoming_assist_message(raw_bytes=raw, source_addr=None)
        self.assertTrue(result.ok, result.reason)

        # The routing-read trust must come from the local reputation engine
        # (default 0.5 for a fresh peer), NOT the self-declared 1.0.
        self.assertAlmostEqual(self._routing_trust(sender) or -1.0, 0.5, places=6)
        self.assertNotAlmostEqual(self._routing_trust(sender) or -1.0, 1.0, places=6)
        # The self-declared value is retained only in the side column.
        self.assertAlmostEqual(self._self_reported_trust(sender) or -1.0, 1.0, places=6)

    def test_register_capability_ad_does_not_overwrite_peer_trust(self) -> None:
        agent = "trusted-agent-id-000000000000"
        # Establish a low, engine-owned trust for the peer.
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO peers (peer_id, trust_score, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (agent, 0.20, _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()

        ad = CapabilityAd(
            agent_id=agent,
            status="idle",
            capabilities=["code"],
            capacity=4,
            trust_score=1.0,
            timestamp=datetime.now(timezone.utc),
        )
        register_capability_ad(ad)

        # peers.trust_score (the reputation-engine column) is untouched by the ad.
        self.assertAlmostEqual(peer_trust(agent), 0.20, places=6)
        # The routing-read agent_capabilities.trust_score is seeded from it.
        self.assertAlmostEqual(self._routing_trust(agent) or -1.0, 0.20, places=6)
        # The self-declared 1.0 lands only in the side column.
        self.assertAlmostEqual(self._self_reported_trust(agent) or -1.0, 1.0, places=6)


class SpawnLimitedWorkerTests(unittest.TestCase):
    """Defect (2): permit leak when Thread.start() raises."""

    class _FakeDaemon:
        def __init__(self, limit: int) -> None:
            self._local_worker_limit = limit
            self._local_worker_sem = threading.BoundedSemaphore(limit)

    def test_thread_start_failure_releases_permit(self) -> None:
        daemon = self._FakeDaemon(limit=2)

        def _noop() -> None:  # pragma: no cover - never invoked on failure path
            pass

        with mock.patch.object(
            daemon_tasks.threading.Thread,
            "start",
            side_effect=RuntimeError("can't start new thread"),
        ):
            ok = daemon_tasks.spawn_limited_worker(
                daemon,
                target=_noop,
                args=(),
                name="w",
                target_id="t",
            )
        self.assertFalse(ok)

        # Both permits must still be available: the failed spawn must not leak one.
        acquired = [daemon._local_worker_sem.acquire(blocking=False) for _ in range(2)]
        self.assertEqual(acquired, [True, True])
        self.assertFalse(daemon._local_worker_sem.acquire(blocking=False))

    def test_repeated_start_failures_do_not_bleed_capacity(self) -> None:
        daemon = self._FakeDaemon(limit=3)
        with mock.patch.object(
            daemon_tasks.threading.Thread,
            "start",
            side_effect=RuntimeError("boom"),
        ):
            for _ in range(10):
                self.assertFalse(
                    daemon_tasks.spawn_limited_worker(
                        daemon, target=lambda: None, args=(), name="w", target_id="t"
                    )
                )

        # Full capacity remains after many failed spawns.
        acquired = [daemon._local_worker_sem.acquire(blocking=False) for _ in range(3)]
        self.assertEqual(acquired, [True, True, True])


class RateLimiterBoundednessTests(unittest.TestCase):
    """Defect (3): _EVENTS / _BREACHES must not grow without bound."""

    def setUp(self) -> None:
        run_migrations()
        with rate_limiter._LOCK:
            rate_limiter._EVENTS.clear()
            rate_limiter._BREACHES.clear()

    def tearDown(self) -> None:
        with rate_limiter._LOCK:
            rate_limiter._EVENTS.clear()
            rate_limiter._BREACHES.clear()

    def test_distinct_keys_stay_bounded(self) -> None:
        with mock.patch.object(rate_limiter, "_MAX_TRACKED_KEYS", 100):
            for i in range(1000):
                rate_limiter.allow_anonymous(f"client-{i}")

            with rate_limiter._LOCK:
                self.assertLessEqual(len(rate_limiter._EVENTS), 100)
                # _BREACHES never exceeds the keys we track.
                self.assertLessEqual(len(rate_limiter._BREACHES), 100)

    def test_stale_keys_are_evicted(self) -> None:
        with mock.patch.object(rate_limiter, "_MAX_TRACKED_KEYS", 5):
            # Fill with stale events (timestamps far in the past).
            past = 0.0
            with rate_limiter._LOCK:
                for i in range(5):
                    rate_limiter._EVENTS[f"old-{i}"].append(past)

            # A new distinct key triggers eviction of the fully-drained windows.
            rate_limiter.allow_anonymous("fresh-client")

            with rate_limiter._LOCK:
                self.assertLessEqual(len(rate_limiter._EVENTS), 5)
                # The brand-new fresh key survived; stale ones were dropped.
                fresh_key = rate_limiter.anon_nullifier("fresh-client")
                self.assertIn(fresh_key, rate_limiter._EVENTS)


if __name__ == "__main__":
    unittest.main()
