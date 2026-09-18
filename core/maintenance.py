from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from core import audit_logger
from core.adaptation_autopilot import schedule_adaptation_autopilot_tick
from core.bootstrap_adapters import BootstrapMirrorAdapter
from core.bootstrap_sync import prune_expired_topic_files, publish_local_presence_snapshots, sync_from_bootstrap_topics
from core.control_plane_workspace import sync_control_plane_workspace
from core.credit_ledger import award_presence_credits
from core.discovery_index import (
    note_peer_endpoint_candidate_probe_result,
    prune_stale_capabilities,
    recent_peer_endpoint_candidates,
    recent_peer_verified_endpoints,
)
from core.hardware_challenge import initiate_random_hardware_challenge
from core.knowledge_freshness import iso_now
from core.reward_engine import finalize_confirmed_rewards, release_mature_pending_rewards
from core.timeout_policy import reap_stale_subtasks
from retrieval.swarm_query import broadcast_capability_ad
from storage.knowledge_index import prune_expired_presence
from storage.replica_table import prune_expired_holders


@dataclass
class MaintenanceConfig:
    tick_seconds: int = 30
    bootstrap_topics: list[str] | None = None
    bootstrap_adapter: BootstrapMirrorAdapter | None = None
    publish_ttl_minutes: int = 15
    stale_capability_hours: int = 24
    topic_file_max_age_minutes: int = 60
    stale_subtask_scan_limit: int = 200
    dht_discovery_min_nodes: int = 20
    dht_discovery_verified_limit: int = 10
    dht_discovery_candidate_limit: int = 4
    dht_discovery_min_verified_endpoints: int = 3
    dht_candidate_probe_cooldown_seconds: int = 300
    dht_candidate_probe_failure_limit: int = 3
    max_failure_backoff_seconds: int = 300


def _dht_discovery_targets(
    *,
    local_peer_id: str,
    verified_limit: int,
    candidate_limit: int,
    min_verified_endpoints: int,
    candidate_probe_cooldown_seconds: int,
    candidate_probe_failure_limit: int,
) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int, str]]]:
    verified_rows = list(
        recent_peer_verified_endpoints(
            exclude_peer_id=local_peer_id,
            limit=max(1, int(verified_limit)),
            per_peer_limit=2,
        )
    )
    verified_targets = [(row.peer_id, row.host, int(row.port)) for row in verified_rows]
    if len(verified_targets) >= max(0, int(min_verified_endpoints)):
        return verified_targets, []

    seen_endpoints = {(host, int(port)) for _, host, port in verified_targets}
    candidate_targets: list[tuple[str, str, int, str]] = []
    for candidate in recent_peer_endpoint_candidates(
        exclude_peer_id=local_peer_id,
        limit=max(1, int(candidate_limit)) * 3,
        cooldown_seconds=int(candidate_probe_cooldown_seconds),
        max_consecutive_failures=int(candidate_probe_failure_limit),
    ):
        endpoint = (candidate.host, int(candidate.port))
        if endpoint in seen_endpoints:
            continue
        candidate_targets.append((candidate.peer_id, candidate.host, int(candidate.port), str(candidate.source)))
        seen_endpoints.add(endpoint)
        if len(candidate_targets) >= max(0, int(candidate_limit)):
            break
    return verified_targets, candidate_targets


class MaintenanceLoop:
    def __init__(
        self,
        *,
        config: MaintenanceConfig,
        capability_ad_builder: Callable[[], bytes] | None = None,
        presence_broadcast: Callable[[], int] | None = None,
        knowledge_broadcast: Callable[[], int] | None = None,
        local_bind_host: str = "127.0.0.1",
        local_bind_port: int = 49152,
    ) -> None:
        self.config = config
        self.capability_ad_builder = capability_ad_builder
        self.presence_broadcast = presence_broadcast
        self.knowledge_broadcast = knowledge_broadcast
        self.local_bind_host = local_bind_host
        self.local_bind_port = int(local_bind_port)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="vool-maintenance-loop",
            daemon=False,  # keep process alive
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def run_tick(self) -> None:
        topics = self.config.bootstrap_topics

        # 1) publish signed bootstrap snapshot
        try:
            publish_local_presence_snapshots(
                topic_names=topics,
                ttl_minutes=self.config.publish_ttl_minutes,
                adapter=self.config.bootstrap_adapter,
            )
        except Exception as e:
            audit_logger.log(
                "maintenance_publish_error",
                target_id="bootstrap",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 2) sync mirrors back into local discovery index
        try:
            sync_from_bootstrap_topics(
                topic_names=topics,
                adapter=self.config.bootstrap_adapter,
            )
        except Exception as e:
            audit_logger.log(
                "maintenance_sync_error",
                target_id="bootstrap",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 2.5) Phase 23: Initiate DHT Discovery
        try:
            from network.assist_router import build_find_node_message
            from network.dht import get_routing_table
            from network.signer import get_local_peer_id
            from network.transport import send_message

            table = get_routing_table()
            table.prune_stale_nodes(max_age_seconds=float(self.config.stale_capability_hours) * 3600.0)
            if len(table.nodes) < max(1, int(self.config.dht_discovery_min_nodes)):
                local_peer_id = get_local_peer_id()
                verified_targets, candidate_targets = _dht_discovery_targets(
                    local_peer_id=local_peer_id,
                    verified_limit=int(self.config.dht_discovery_verified_limit),
                    candidate_limit=int(self.config.dht_discovery_candidate_limit),
                    min_verified_endpoints=int(self.config.dht_discovery_min_verified_endpoints),
                    candidate_probe_cooldown_seconds=int(self.config.dht_candidate_probe_cooldown_seconds),
                    candidate_probe_failure_limit=int(self.config.dht_candidate_probe_failure_limit),
                )
                payload = build_find_node_message(local_peer_id)
                verified_sent = 0
                candidate_sent = 0
                for _, host, port in verified_targets:
                    if send_message(host, int(port), payload):
                        verified_sent += 1
                for peer_id, host, port, source in candidate_targets:
                    delivered = bool(send_message(host, int(port), payload))
                    note_peer_endpoint_candidate_probe_result(
                        peer_id,
                        host,
                        int(port),
                        source=source,
                        delivered=delivered,
                    )
                    if delivered:
                        candidate_sent += 1
                sent = verified_sent + candidate_sent
                if sent > 0:
                    audit_logger.log(
                        "dht_discovery_initiated",
                        target_id="dht",
                        target_type="maintenance",
                        details={
                            "sent": sent,
                            "verified_sent": verified_sent,
                            "candidate_sent": candidate_sent,
                            "verified_target_count": len(verified_targets),
                            "candidate_target_count": len(candidate_targets),
                        },
                    )
        except Exception as e:
            audit_logger.log("dht_discovery_error", target_id="dht", target_type="maintenance", details={"error": str(e)})

        # 3) refresh peer ads outward
        if self.capability_ad_builder:
            try:
                raw = self.capability_ad_builder()
                broadcast_capability_ad(raw, limit=25)
            except Exception as e:
                audit_logger.log(
                    "maintenance_capability_broadcast_error",
                    target_id="assist",
                    target_type="maintenance",
                    details={"error": str(e)},
                )

        # 3.5) presence heartbeat + earn presence credits
        if self.presence_broadcast:
            try:
                self.presence_broadcast()
                from core import policy_engine as _pe
                from network.signer import get_local_peer_id as _local_id
                tick_ts = int(time.time())
                award_presence_credits(
                    _local_id(),
                    amount=float(_pe.get("economics.presence_credit_per_tick", 0.10)),
                    receipt_id=f"presence:{_local_id()}:{tick_ts // 300}",
                )
            except Exception as e:
                audit_logger.log(
                    "maintenance_presence_broadcast_error",
                    target_id="presence",
                    target_type="maintenance",
                    details={"error": str(e)},
                )

        # 3.6) knowledge advertisements
        if self.knowledge_broadcast:
            try:
                self.knowledge_broadcast()
            except Exception as e:
                audit_logger.log(
                    "maintenance_knowledge_broadcast_error",
                    target_id="knowledge",
                    target_type="maintenance",
                    details={"error": str(e)},
                )

        # 3.7) knowledge replication — request copies for under-replicated shards
        try:
            from core.knowledge_replication import under_replicated_shards
            from network.signer import get_local_peer_id as _repl_peer
            from retrieval.swarm_query import dispatch_query_shard

            under_rep = under_replicated_shards(limit=10)
            repl_requested = 0
            for item in under_rep:
                try:
                    dispatch_query_shard(
                        str(item["shard_id"]),
                        requester_peer_id=_repl_peer(),
                    )
                    repl_requested += 1
                except Exception:
                    pass
            if repl_requested:
                audit_logger.log(
                    "knowledge_replication_tick",
                    target_id="knowledge",
                    target_type="maintenance",
                    details={"under_replicated": len(under_rep), "requested": repl_requested},
                )
        except Exception as e:
            audit_logger.log(
                "knowledge_replication_error",
                target_id="knowledge",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 3.8) knowledge freshness audit — sample holders and challenge them
        try:
            from core import policy_engine as _fa_pe
            from core.knowledge_freshness_audit import select_holders_for_sampling, start_sampling_audit

            tick_count = getattr(self, "_tick_count", 0) + 1
            self._tick_count = tick_count
            audit_interval = int(_fa_pe.get("shards.freshness_audit_interval_ticks", 10))
            if tick_count % audit_interval == 0:
                holders = select_holders_for_sampling(limit=4)
                audits_started = 0
                for holder in holders:
                    try:
                        start_sampling_audit(
                            shard_id=str(holder["shard_id"]),
                            holder_peer_id=str(holder["holder_peer_id"]),
                            trigger_reason="scheduled_maintenance",
                        )
                        audits_started += 1
                    except Exception:
                        pass
                if audits_started:
                    audit_logger.log(
                        "knowledge_freshness_audit_tick",
                        target_id="knowledge",
                        target_type="maintenance",
                        details={"sampled": len(holders), "audits_started": audits_started},
                    )
        except Exception as e:
            audit_logger.log(
                "knowledge_freshness_audit_error",
                target_id="knowledge",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 4) prune stale capabilities
        try:
            pruned_caps = prune_stale_capabilities(max_age_hours=self.config.stale_capability_hours)
        except Exception as e:
            pruned_caps = 0
            audit_logger.log(
                "maintenance_prune_caps_error",
                target_id="discovery",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 4.5) prune expired presence leases and stale holder claims
        try:
            pruned_presence = prune_expired_presence(iso_now())
            pruned_holders = prune_expired_holders(iso_now())
        except Exception as e:
            pruned_presence = 0
            pruned_holders = 0
            audit_logger.log(
                "maintenance_knowledge_prune_error",
                target_id="knowledge",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 5) release mature pending rewards
        try:
            timed_out_subtasks = reap_stale_subtasks(limit=int(self.config.stale_subtask_scan_limit))
        except Exception as e:
            timed_out_subtasks = 0
            audit_logger.log(
                "maintenance_timeout_reaper_error",
                target_id="task_state",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 6) release mature pending rewards
        try:
            released = release_mature_pending_rewards(limit=100)
        except Exception as e:
            released = 0
            audit_logger.log(
                "maintenance_reward_release_error",
                target_id="rewards",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 6.5) advance confirmed rewards to finality or slash challenged work
        try:
            finalized = finalize_confirmed_rewards(limit=100)
        except Exception as e:
            finalized = 0
            audit_logger.log(
                "maintenance_reward_finality_error",
                target_id="rewards",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 7) prune expired bootstrap files
        try:
            removed = prune_expired_topic_files(max_age_minutes=self.config.topic_file_max_age_minutes)
        except Exception as e:
            removed = 0
            audit_logger.log(
                "maintenance_prune_topics_error",
                target_id="bootstrap",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 8) Hardware micro-benchmark spot checks (anti-cheat)
        try:
            initiate_random_hardware_challenge()
        except Exception as e:
            audit_logger.log(
                "maintenance_hardware_challenge_error",
                target_id="anti_cheat",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 9) Background adaptation autopilot tick
        try:
            schedule_adaptation_autopilot_tick()
        except Exception as e:
            audit_logger.log(
                "maintenance_adaptation_autopilot_error",
                target_id="adaptation",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 10) Operator-facing control-plane mirror
        try:
            control_sync = sync_control_plane_workspace()
        except Exception as e:
            control_sync = {"ok": False, "writes": 0}
            audit_logger.log(
                "maintenance_control_plane_sync_error",
                target_id="control_plane",
                target_type="maintenance",
                details={"error": str(e)},
            )

        audit_logger.log(
            "maintenance_tick_complete",
            target_id="maintenance",
            target_type="maintenance",
            details={
                "pruned_caps": pruned_caps,
                "pruned_presence": pruned_presence,
                "pruned_holders": pruned_holders,
                "timed_out_subtasks": timed_out_subtasks,
                "released_rewards": released,
                "finalized_rewards": finalized,
                "removed_topics": removed,
                "control_sync_ok": bool(control_sync.get("ok")),
                "control_sync_writes": int(control_sync.get("writes") or 0),
            },
        )

    def _loop(self) -> None:
        consecutive_failures = 0
        while not self._stop.is_set():
            try:
                self.run_tick()
                consecutive_failures = 0
            except Exception as e:
                # A transient fault (e.g. a momentary sqlite error) must not
                # permanently and silently kill this non-daemon thread. Log it,
                # then back off (bounded) and retry on the next iteration.
                consecutive_failures += 1
                # Even the audit sink may be momentarily unavailable; never let
                # logging the failure become the thing that kills the loop.
                with contextlib.suppress(Exception):
                    audit_logger.log(
                        "maintenance_tick_failed",
                        target_id="maintenance",
                        target_type="maintenance",
                        details={
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "consecutive_failures": consecutive_failures,
                        },
                    )
                if self._backoff_sleep(consecutive_failures):
                    continue

            # responsive sleep
            deadline = time.time() + self.config.tick_seconds
            while time.time() < deadline and not self._stop.is_set():
                time.sleep(0.25)

    def _backoff_sleep(self, consecutive_failures: int) -> bool:
        """Sleep a bounded, exponentially growing interval after a failed tick.

        Returns True if the loop should immediately re-evaluate the stop flag
        (i.e. a stop was requested during the backoff).
        """
        base = max(1, int(self.config.tick_seconds))
        capped_exp = min(int(consecutive_failures), 6)
        backoff_seconds = min(base * (2 ** (capped_exp - 1)), self.config.max_failure_backoff_seconds)
        deadline = time.time() + max(0.0, float(backoff_seconds))
        while time.time() < deadline and not self._stop.is_set():
            time.sleep(0.25)
        return self._stop.is_set()
