from __future__ import annotations

import unittest
from unittest import mock

from core.public_hive_quotas import reserve_public_hive_write_quota
from storage.db import get_connection
from storage.migrations import run_migrations


class PublicHiveQuotaTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM public_hive_write_quota_events")
            conn.execute("DELETE FROM peers")
            conn.execute("DELETE FROM hive_topic_claims")
            conn.execute("DELETE FROM hive_topics")
            conn.commit()
        finally:
            conn.close()

    def test_unknown_peer_uses_newcomer_quota_and_exhausts(self) -> None:
        with mock.patch("core.public_hive_quotas.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.public_hive_unknown_peer_trust": 0.30,
                "economics.public_hive_low_trust_threshold": 0.45,
                "economics.public_hive_high_trust_threshold": 0.75,
                "economics.public_hive_daily_quota_low": 1.0,
                "economics.public_hive_daily_quota_mid": 3.0,
                "economics.public_hive_daily_quota_high": 6.0,
                "economics.public_hive_route_costs": {
                    "/v1/hive/posts": 1.0,
                },
            }.get(path, default)
            first = reserve_public_hive_write_quota("peer-new", "/v1/hive/posts", request_nonce="nonce-1")
            second = reserve_public_hive_write_quota("peer-new", "/v1/hive/posts", request_nonce="nonce-2")
        self.assertTrue(first.allowed)
        self.assertEqual(first.trust_tier, "newcomer")
        self.assertFalse(second.allowed)
        self.assertEqual(second.reason, "daily_public_hive_quota_exhausted")

    def test_claims_require_minimum_trust(self) -> None:
        with mock.patch("core.public_hive_quotas.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.public_hive_unknown_peer_trust": 0.25,
                "economics.public_hive_min_claim_trust": 0.42,
                "economics.public_hive_route_costs": {
                    "/v1/hive/topic-claims": 1.0,
                },
            }.get(path, default)
            reservation = reserve_public_hive_write_quota("peer-claim-low", "/v1/hive/topic-claims", request_nonce="claim-1")
        self.assertFalse(reservation.allowed)
        self.assertEqual(reservation.reason, "insufficient_claim_trust")

    def test_sensitive_commons_routes_require_route_specific_trust(self) -> None:
        with mock.patch("core.public_hive_quotas.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.public_hive_unknown_peer_trust": 0.30,
                "economics.public_hive_route_costs": {
                    "/v1/hive/commons/promotion-reviews": 0.25,
                },
                "economics.public_hive_min_route_trusts": {
                    "/v1/hive/commons/promotion-reviews": 0.75,
                },
            }.get(path, default)
            reservation = reserve_public_hive_write_quota(
                "peer-review-low",
                "/v1/hive/commons/promotion-reviews",
                request_nonce="commons-review-1",
            )
        self.assertFalse(reservation.allowed)
        self.assertEqual(reservation.reason, "insufficient_route_trust")

    def test_trusted_peer_gets_larger_daily_limit(self) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO peers (
                    peer_id, display_alias, trust_score, successful_shards, failed_shards,
                    strike_count, status, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, 0, 0, 0, 'active', datetime('now'), datetime('now'), datetime('now'))
                """,
                ("peer-trusted", None, 0.88),
            )
            conn.commit()
        finally:
            conn.close()
        with mock.patch("core.public_hive_quotas.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.public_hive_low_trust_threshold": 0.45,
                "economics.public_hive_high_trust_threshold": 0.75,
                "economics.public_hive_daily_quota_low": 1.0,
                "economics.public_hive_daily_quota_mid": 2.0,
                "economics.public_hive_daily_quota_high": 4.0,
                "economics.public_hive_route_costs": {
                    "/v1/hive/posts": 1.0,
                },
            }.get(path, default)
            last = None
            for idx in range(4):
                last = reserve_public_hive_write_quota("peer-trusted", "/v1/hive/posts", request_nonce=f"trusted-{idx}")
                self.assertTrue(last.allowed)
            blocked = reserve_public_hive_write_quota("peer-trusted", "/v1/hive/posts", request_nonce="trusted-5")
        self.assertIsNotNone(last)
        self.assertEqual(last.trust_tier, "trusted")
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.limit_points, 4.0)

    def test_active_claims_extend_daily_quota_budget(self) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO hive_topics (
                    topic_id, created_by_agent_id, title, summary, topic_tags_json, status,
                    visibility, evidence_mode, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '[]', 'researching', 'agent_public', 'candidate_only', datetime('now'), datetime('now'))
                """,
                ("topic-bonus", "peer-busy", "Busy topic", "Busy topic summary"),
            )
            conn.execute(
                """
                INSERT INTO hive_topic_claims (
                    claim_id, topic_id, agent_id, status, note, capability_tags_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, '[]', datetime('now'), datetime('now'))
                """,
                ("claim-bonus", "topic-bonus", "peer-busy", "active worker"),
            )
            conn.commit()
        finally:
            conn.close()
        with mock.patch("core.public_hive_quotas.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.public_hive_unknown_peer_trust": 0.25,
                "economics.public_hive_low_trust_threshold": 0.45,
                "economics.public_hive_high_trust_threshold": 0.75,
                "economics.public_hive_daily_quota_low": 1.0,
                "economics.public_hive_daily_quota_mid": 2.0,
                "economics.public_hive_daily_quota_high": 4.0,
                "economics.public_hive_daily_quota_bonus_per_active_claim": 2.0,
                "economics.public_hive_daily_quota_max_active_claim_bonus": 6.0,
                "economics.public_hive_route_costs": {
                    "/v1/hive/posts": 1.0,
                },
            }.get(path, default)
            first = reserve_public_hive_write_quota("peer-busy", "/v1/hive/posts", request_nonce="bonus-1")
            second = reserve_public_hive_write_quota("peer-busy", "/v1/hive/posts", request_nonce="bonus-2")
            third = reserve_public_hive_write_quota("peer-busy", "/v1/hive/posts", request_nonce="bonus-3")
            fourth = reserve_public_hive_write_quota("peer-busy", "/v1/hive/posts", request_nonce="bonus-4")
        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertTrue(third.allowed)
        self.assertFalse(fourth.allowed)
        self.assertEqual(first.limit_points, 3.0)


if __name__ == "__main__":
    unittest.main()
