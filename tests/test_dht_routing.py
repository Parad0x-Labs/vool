from __future__ import annotations

import unittest

from network.dht import RoutingTable


class DhtRoutingTests(unittest.TestCase):
    def test_kbucket_size_is_enforced(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=3, bucket_count=16)
        for i in range(20):
            peer_id = f"{i + 1:064x}"
            table.add_node(peer_id, f"198.51.100.{(i % 200) + 1}", 49000 + i)
        self.assertLessEqual(len(table.get_all_nodes()), 3 * 16)

    def test_find_closest_prefers_xor_distance(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        near = "0" * 63 + "1"
        mid = "0" * 62 + "10"
        far = "f" * 64
        table.add_node(near, "203.0.113.10", 49001)
        table.add_node(mid, "203.0.113.11", 49002)
        table.add_node(far, "203.0.113.12", 49003)

        out = table.find_closest_peers("0" * 64, count=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].peer_id, near)
        self.assertIn(out[1].peer_id, {mid, far})

    def test_prune_stale_nodes(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        stale_peer = "a" * 64
        fresh_peer = "b" * 64
        table.add_node(stale_peer, "198.51.100.10", 49010)
        table.add_node(fresh_peer, "198.51.100.11", 49011)
        table.nodes[stale_peer].last_seen = 0.0

        removed = table.prune_stale_nodes(max_age_seconds=10.0)
        self.assertEqual(removed, 1)
        self.assertNotIn(stale_peer, {n.peer_id for n in table.get_all_nodes()})
        self.assertIn(fresh_peer, {n.peer_id for n in table.get_all_nodes()})

    def test_find_lookup_candidates_excludes_contacted_peers(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        near = "0" * 63 + "1"
        mid = "0" * 62 + "10"
        far = "f" * 64
        table.add_node(near, "203.0.113.10", 49001)
        table.add_node(mid, "203.0.113.11", 49002)
        table.add_node(far, "203.0.113.12", 49003)

        out = table.find_lookup_candidates("0" * 64, count=2, exclude_peer_ids={near})
        self.assertEqual(len(out), 2)
        self.assertNotIn(near, {item.peer_id for item in out})
        self.assertEqual(out[0].peer_id, mid)

    def test_find_lookup_candidates_prefers_fresh_peers_over_stale_ones(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        stale_near = "0" * 63 + "1"
        fresh_far = "f" * 64
        table.add_node(stale_near, "203.0.113.10", 49001)
        table.add_node(fresh_far, "203.0.113.11", 49002)
        table.nodes[stale_near].last_seen = 0.0
        table._stale_node_age_seconds = 10.0

        out = table.find_lookup_candidates("0" * 64, count=2, now=1000.0)

        self.assertEqual([item.peer_id for item in out], [fresh_far, stale_near])

    def test_find_lookup_candidates_keeps_stale_peers_as_fallback_when_needed(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        stale_only = "0" * 63 + "1"
        table.add_node(stale_only, "203.0.113.10", 49001)
        table.nodes[stale_only].last_seen = 0.0
        table._stale_node_age_seconds = 10.0

        out = table.find_lookup_candidates("0" * 64, count=1, now=1000.0)

        self.assertEqual([item.peer_id for item in out], [stale_only])

    def test_refresh_targets_returns_stale_buckets_in_age_order(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=16)
        near = "0" * 63 + "1"
        mid = "0" * 60 + "0010"
        table.add_node(near, "203.0.113.10", 49001)
        table.add_node(mid, "203.0.113.11", 49002)

        near_bucket = table._bucket_index(near)
        mid_bucket = table._bucket_index(mid)
        assert near_bucket is not None
        assert mid_bucket is not None
        table._bucket_touched_at[near_bucket] = 985.0
        table._bucket_touched_at[mid_bucket] = 960.0

        targets = table.refresh_targets(max_age_seconds=10.0, limit=2, now=1000.0)

        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].bucket_index, mid_bucket)
        self.assertEqual(targets[1].bucket_index, near_bucket)
        self.assertEqual(len(targets[0].target_id), 64)
        self.assertNotEqual(targets[0].target_id, table.local_peer_id)

    def test_add_node_does_not_evict_fresh_bucket_incumbents_when_bucket_is_full(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=2, bucket_count=16)
        first = f"{8:064x}"
        second = f"{9:064x}"
        challenger = f"{10:064x}"
        table.add_node(first, "203.0.113.10", 49001)
        table.add_node(second, "203.0.113.11", 49002)

        bucket_index = table._bucket_index(first)
        assert bucket_index is not None
        table.add_node(challenger, "203.0.113.12", 49003)

        self.assertEqual(list(table._buckets[bucket_index].keys()), [first, second])
        self.assertNotIn(challenger, table.nodes)
        self.assertIn(challenger, table._replacement_caches[bucket_index])

    def test_add_node_replaces_stale_bucket_incumbent_when_bucket_is_full(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=2, bucket_count=16)
        first = f"{8:064x}"
        second = f"{9:064x}"
        challenger = f"{10:064x}"
        table.add_node(first, "203.0.113.10", 49001)
        table.add_node(second, "203.0.113.11", 49002)
        table.nodes[first].last_seen = 0.0

        bucket_index = table._bucket_index(first)
        assert bucket_index is not None
        table.add_node(challenger, "203.0.113.12", 49003)

        self.assertNotIn(first, table.nodes)
        self.assertIn(challenger, table.nodes)
        self.assertEqual(list(table._buckets[bucket_index].keys()), [second, challenger])
        self.assertNotIn(challenger, table._replacement_caches[bucket_index])

    def test_prune_stale_nodes_promotes_waiting_replacement_candidate(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=2, bucket_count=16)
        first = f"{8:064x}"
        second = f"{9:064x}"
        challenger = f"{10:064x}"
        table.add_node(first, "203.0.113.10", 49001)
        table.add_node(second, "203.0.113.11", 49002)
        bucket_index = table._bucket_index(first)
        assert bucket_index is not None
        table.add_node(challenger, "203.0.113.12", 49003)
        table.nodes[first].last_seen = 0.0

        removed = table.prune_stale_nodes(max_age_seconds=10.0)

        self.assertEqual(removed, 1)
        self.assertNotIn(first, table.nodes)
        self.assertIn(challenger, table.nodes)
        self.assertEqual(list(table._buckets[bucket_index].keys()), [second, challenger])
        self.assertEqual(len(table._replacement_caches[bucket_index]), 0)

    def test_add_node_does_not_replace_observed_endpoint_with_weaker_dht_source(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        peer_id = "a" * 64

        table.add_node(peer_id, "203.0.113.10", 49001, source="observed")
        table.add_node(peer_id, "198.51.100.99", 49999, source="dht")

        node = table.nodes[peer_id]
        self.assertEqual((node.ip, node.port), ("203.0.113.10", 49001))
        self.assertEqual(node.endpoint_source, "observed")

    def test_add_node_allows_stronger_observed_endpoint_to_replace_weaker_dht_source(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        peer_id = "b" * 64

        table.add_node(peer_id, "198.51.100.99", 49999, source="dht")
        table.add_node(peer_id, "203.0.113.10", 49001, source="observed")

        node = table.nodes[peer_id]
        self.assertEqual((node.ip, node.port), ("203.0.113.10", 49001))
        self.assertEqual(node.endpoint_source, "observed")

    def test_find_closest_peers_verified_only_skips_unverified_referrals(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        referral_peer = "0" * 63 + "1"
        observed_peer = "f" * 64

        table.add_node(referral_peer, "198.51.100.99", 49999, source="dht")
        table.add_node(observed_peer, "203.0.113.10", 49001, source="observed")

        out = table.find_closest_peers("0" * 64, count=2, verified_only=True)

        self.assertEqual([item.peer_id for item in out], [observed_peer])

    def test_weaker_referral_does_not_refresh_observed_liveness_or_bucket_touch(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        peer_id = "c" * 64
        table.add_node(peer_id, "203.0.113.10", 49001, source="observed")

        bucket_index = table._bucket_index(peer_id)
        assert bucket_index is not None
        original_last_seen = table.nodes[peer_id].last_seen
        original_bucket_touch = table._bucket_touched_at[bucket_index]

        table.add_node(peer_id, "198.51.100.99", 49999, source="dht")

        node = table.nodes[peer_id]
        self.assertEqual((node.ip, node.port), ("203.0.113.10", 49001))
        self.assertEqual(node.endpoint_source, "observed")
        self.assertEqual(node.last_seen, original_last_seen)
        self.assertEqual(table._bucket_touched_at[bucket_index], original_bucket_touch)

    def test_referral_only_node_stays_stale_until_verified(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        peer_id = "d" * 64
        table.add_node(peer_id, "198.51.100.99", 49999, source="dht")

        bucket_index = table._bucket_index(peer_id)
        assert bucket_index is not None
        self.assertEqual(table.nodes[peer_id].last_seen, 0.0)
        self.assertEqual(table._bucket_touched_at[bucket_index], 0.0)

        table.add_node(peer_id, "203.0.113.10", 49001, source="observed")

        self.assertEqual((table.nodes[peer_id].ip, table.nodes[peer_id].port), ("203.0.113.10", 49001))
        self.assertEqual(table.nodes[peer_id].endpoint_source, "observed")
        self.assertGreater(table.nodes[peer_id].last_seen, 0.0)
        self.assertGreater(table._bucket_touched_at[bucket_index], 0.0)


if __name__ == "__main__":
    unittest.main()
