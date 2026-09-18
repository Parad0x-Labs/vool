"""The VOOL mesh daemon (MILESTONE 1: runtime-owned, core-owned home).

Moved from `apps/vool_daemon.py`, which is now a thin CLI facade. REMOVAL CONDITION
for the facade's re-export: once every caller (patch sites included) targets this
module, the facade shrinks to `main()` alone.
"""
from __future__ import annotations

import argparse

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import signal
import sys
import threading
from datetime import datetime, timezone
from typing import Any


from core import audit_logger, policy_engine
from core.capability_tokens import expire_stale_capability_tokens
from core.daemon import DaemonConfig, NodeRuntime
from core.daemon import is_loopback_host as _is_loopback_host
from core.daemon.health import health_snapshot, start_health_server
from core.daemon.mesh import (
    accepts_hive_tasks,
    active_assignment_count,
    assign_pending_claims_for_open_offers,
    build_capability_ad_message_payload,
    effective_assist_status,
    idle_assist_config,
    rebroadcast_parent_offers,
    reconcile_mesh_state,
    refresh_advertised_capacity,
    refresh_assist_status,
    requeue_stale_parent_assignments,
    resume_incomplete_parent_tasks,
    run_order_book_loop,
)
from core.daemon.messages import (
    handle_query_shard,
    handle_report_abuse,
    handle_request_shard,
    handle_shard_candidates,
    handle_shard_payload,
    on_message,
    reply_basic,
    reporter_related_to_task,
)
from core.daemon.tasks import (
    decode_verified_assist_envelope,
    maybe_auto_review_result_from_raw,
    maybe_execute_local_assignment_from_raw,
    send_or_log,
    spawn_limited_worker,
)
from core.discovery_index import endpoint_for_peer, peer_trust, register_capability_ad, register_peer_endpoint
from core.helper_scheduler import HelperScheduler, SchedulerConfig
from core.knowledge_advertiser import broadcast_hello, broadcast_local_knowledge_ads, broadcast_presence_heartbeat
from core.knowledge_registry import sync_local_learning_shards
from core.local_worker_pool import resolve_local_worker_capacity
from core.logging_config import setup_logging
from core.maintenance import MaintenanceConfig, MaintenanceLoop
from core.parent_orchestrator import continue_parent_orchestration
from core.result_reviewer import auto_review_task_result
from core.timeout_policy import reap_stale_subtasks
from core.user_preferences import hive_task_intake_enabled
from network.assist_models import AssistFilters, CapabilityAd
from network.assist_router import load_task_capsule_for_task
from network.pow_hashcash import generate_pow, required_pow_difficulty
from network.signer import get_local_peer_id as local_peer_id
from network.transport import UDPTransportServer
from sandbox.helper_worker import run_task_capsule

_THIS_MODULE = sys.modules[__name__]
_COMPAT_EXPORTS = (
    endpoint_for_peer,
    peer_trust,
    continue_parent_orchestration,
    auto_review_task_result,
    reap_stale_subtasks,
    hive_task_intake_enabled,
    expire_stale_capability_tokens,
    load_task_capsule_for_task,
    run_task_capsule,
)


class VoolDaemon:
    def __init__(self, config: DaemonConfig | None = None):
        self.config = config or DaemonConfig()
        self.transport: UDPTransportServer | None = None
        self.maintenance: MaintenanceLoop | None = None
        self.local_capability_ad: CapabilityAd | None = None
        self._order_book_running = False
        self._order_book_thread: threading.Thread | None = None
        self._health_server = None
        self._health_thread: threading.Thread | None = None
        self._runtime = None
        initial_workers = int(self.config.local_worker_threads or max(2, int(self.config.capacity) * 2))
        self._local_worker_limit = max(1, initial_workers)
        self._local_worker_sem = threading.BoundedSemaphore(self._local_worker_limit)
        self._helper_scheduler = HelperScheduler(
            SchedulerConfig(
                max_concurrent_mesh_tasks=max(1, int(self.config.capacity)),
                reserve_capacity_for_local_user=True,
            )
        )

    def start(self) -> NodeRuntime:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        configured_worker_limit = int(
            self.config.local_worker_threads
            if self.config.local_worker_threads is not None
            else policy_engine.get("runtime.max_local_worker_threads", max(2, int(self.config.capacity) * 2))
        )
        self._local_worker_limit = max(1, configured_worker_limit)
        self._local_worker_sem = threading.BoundedSemaphore(self._local_worker_limit)
        self._helper_scheduler = HelperScheduler(
            SchedulerConfig(
                max_concurrent_mesh_tasks=max(1, int(self.config.capacity)),
                reserve_capacity_for_local_user=bool(
                    policy_engine.get("assist_mesh.reserve_capacity_for_local_user", True)
                ),
            )
        )
        if not str(self.config.health_auth_token or "").strip():
            token = str(policy_engine.get("runtime.health_auth_token", "") or "").strip()
            self.config.health_auth_token = token or None
        if int(self.config.health_bind_port) > 0 and not _is_loopback_host(self.config.health_bind_host):
            if not str(self.config.health_auth_token or "").strip():
                raise ValueError("Non-loopback daemon health endpoint requires a health_auth_token.")

        audit_logger.log(
            "daemon_genesis_pow",
            target_id=local_peer_id(),
            target_type="daemon",
            details={"status": "calculating"},
        )
        pow_difficulty = required_pow_difficulty(default=4)
        genesis_nonce = generate_pow(local_peer_id(), target_difficulty=pow_difficulty)
        self._refresh_assist_status()

        self.local_capability_ad = CapabilityAd(
            agent_id=local_peer_id(),
            status=self.config.assist_status,
            capabilities=self.config.capabilities,
            compute_class=self.config.compute_class,
            supported_models=self.config.supported_models,
            capacity=self.config.capacity,
            trust_score=0.50,
            assist_filters=AssistFilters(
                allow_research=True,
                allow_code_reasoning=False,
                allow_validation=True,
                min_reward_points=0,
                trusted_peers_only=False,
                host_group_hint_hash=self.config.local_host_group_hint_hash,
            ),
            pow_difficulty=pow_difficulty,
            genesis_nonce=genesis_nonce,
            timestamp=datetime.now(timezone.utc),
        )
        register_capability_ad(self.local_capability_ad)
        sync_local_learning_shards(limit=500)

        self.transport = UDPTransportServer(
            host=self.config.bind_host,
            port=self.config.bind_port,
            on_message=self._on_message,
        )
        runtime = self.transport.start()
        self._runtime = runtime
        self.config.bind_port = int(runtime.port)
        from network.bootstrap_node import upsert_bootstrap_peer
        from network.nat_probe import classify_nat, detect_local_host
        from network.relay_fallback import choose_relay_mode

        nat_probe = classify_nat(detect_local_host(), runtime.public_host, runtime.public_port)
        relay_mode = choose_relay_mode(
            direct_reachable=nat_probe.mode == "wan_direct",
            relay_available=False,
            peer_id=local_peer_id(),
            nat_mode=nat_probe.mode,
        )

        configured_advertise = str(self.config.advertise_host or "").strip()
        if configured_advertise and configured_advertise not in {"127.0.0.1", "localhost", "0.0.0.0"}:
            advertised_host = configured_advertise
            advertised_port = int(runtime.port)
        else:
            advertised_host = runtime.public_host
            advertised_port = runtime.public_port
        register_peer_endpoint(local_peer_id(), advertised_host, advertised_port, source="self")
        upsert_bootstrap_peer(local_peer_id(), advertised_host, advertised_port, relay_mode.mode)

        self.maintenance = MaintenanceLoop(
            config=MaintenanceConfig(
                tick_seconds=self.config.maintenance_tick_seconds,
                bootstrap_topics=self.config.bootstrap_topics,
                bootstrap_adapter=self.config.bootstrap_adapter,
            ),
            capability_ad_builder=self._build_capability_ad_message,
            presence_broadcast=lambda: broadcast_presence_heartbeat(
                capabilities=self.config.capabilities,
                status=self.config.assist_status,
                transport_mode=relay_mode.mode,
            ),
            knowledge_broadcast=lambda: broadcast_local_knowledge_ads(),
            local_bind_host=advertised_host,
            local_bind_port=advertised_port,
        )
        self.maintenance.start()
        broadcast_hello(
            agent_name=None,
            capabilities=self.config.capabilities,
            status=self.config.assist_status,
            transport_mode=relay_mode.mode,
        )
        broadcast_local_knowledge_ads()

        self._order_book_running = True
        self._order_book_thread = threading.Thread(target=self._run_order_book_loop, daemon=True)
        self._order_book_thread.start()
        self._start_health_server()

        audit_logger.log(
            "daemon_started",
            target_id=f"{runtime.public_host}:{runtime.public_port}",
            target_type="daemon",
            details={
                "peer_id": local_peer_id(),
                "local_bind": f"{self.config.bind_host}:{self.config.bind_port}",
                "stun_public": f"{runtime.public_host}:{runtime.public_port}",
                "nat_mode": nat_probe.mode,
                "connectivity_mode": relay_mode.mode,
            },
        )

        return NodeRuntime(
            host=runtime.host,
            port=runtime.port,
            public_host=runtime.public_host,
            public_port=runtime.public_port,
            running=True,
        )

    def stop(self) -> None:
        if self.maintenance:
            self.maintenance.stop()
        if self.transport:
            self.transport.stop()

        self._order_book_running = False
        if self._order_book_thread:
            self._order_book_thread.join(timeout=2.0)
        if self._health_server:
            if hasattr(self._health_server, "should_exit"):
                self._health_server.should_exit = True
            shutdown = getattr(self._health_server, "shutdown", None)
            if callable(shutdown):
                shutdown()
            server_close = getattr(self._health_server, "server_close", None)
            if callable(server_close):
                server_close()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=2.0)

        audit_logger.log(
            "daemon_stopped",
            target_id=local_peer_id(),
            target_type="daemon",
            details={},
        )

    def _start_health_server(self) -> None:
        start_health_server(self)

    def _health_snapshot(self) -> dict[str, object]:
        return health_snapshot(self)

    def _run_order_book_loop(self) -> None:
        run_order_book_loop(self)

    def _build_capability_ad_message(self) -> bytes:
        return build_capability_ad_message_payload(self)

    def _on_message(self, raw: bytes, addr: tuple[str, int]) -> None:
        on_message(self, raw, addr)

    def _reply_basic(self, msg_type: str, payload: dict[str, object], addr: tuple[str, int]) -> None:
        reply_basic(self, msg_type, payload, addr)

    def _handle_report_abuse(self, payload: dict[str, object], sender: str, addr: tuple[str, int]) -> None:
        handle_report_abuse(self, payload, sender, addr)

    def _reporter_related_to_task(self, reporter_peer_id: str, task_id: str) -> bool:
        return reporter_related_to_task(reporter_peer_id, task_id)

    def _active_assignment_count(self) -> int:
        return active_assignment_count(self)

    def _accepts_hive_tasks(self) -> bool:
        return accepts_hive_tasks(hooks=_THIS_MODULE)

    def _effective_assist_status(self) -> str:
        return effective_assist_status(self, hooks=_THIS_MODULE)

    def _refresh_assist_status(self) -> bool:
        return refresh_assist_status(self, hooks=_THIS_MODULE)

    def _idle_assist_config(self):
        return idle_assist_config(self)

    def _refresh_advertised_capacity(self) -> int:
        return refresh_advertised_capacity(self)

    def _requeue_stale_parent_assignments(self, *, limit: int = 20) -> list[str]:
        return requeue_stale_parent_assignments(self, limit=limit)

    def _assign_pending_claims_for_open_offers(self, *, limit: int = 20) -> int:
        return assign_pending_claims_for_open_offers(self, hooks=_THIS_MODULE, limit=limit)

    def _rebroadcast_parent_offers(self, task_ids: list[str]) -> int:
        return rebroadcast_parent_offers(self, task_ids)

    def _resume_incomplete_parent_tasks(self, *, limit: int = 20) -> int:
        return resume_incomplete_parent_tasks(hooks=_THIS_MODULE, limit=limit)

    def _reconcile_mesh_state(self) -> None:
        reconcile_mesh_state(self, hooks=_THIS_MODULE)

    def _handle_query_shard(self, payload: dict[str, object], addr: tuple[str, int]) -> None:
        handle_query_shard(self, payload, addr)

    def _handle_shard_candidates(self, payload: dict[str, object], sender_peer_id: str) -> None:
        handle_shard_candidates(self, payload, sender_peer_id)

    def _handle_request_shard(self, payload: dict[str, object], addr: tuple[str, int]) -> None:
        handle_request_shard(self, payload, addr)

    def _handle_shard_payload(self, payload: dict[str, object], sender_peer_id: str) -> None:
        handle_shard_payload(self, payload, sender_peer_id)

    def _maybe_auto_review_result_from_raw(self, raw: bytes, fallback_addr: tuple[str, int]) -> None:
        maybe_auto_review_result_from_raw(self, raw, fallback_addr, hooks=_THIS_MODULE)

    def _maybe_execute_local_assignment_from_raw(self, raw: bytes, fallback_addr: tuple[str, int]) -> None:
        maybe_execute_local_assignment_from_raw(self, raw, fallback_addr, hooks=_THIS_MODULE)

    def _decode_verified_assist_envelope(self, raw: bytes, *, expected_msg_type: str):
        return decode_verified_assist_envelope(raw, expected_msg_type=expected_msg_type)

    def _spawn_limited_worker(
        self,
        *,
        target: Any,
        args: tuple[Any, ...],
        name: str,
        target_id: str,
    ) -> bool:
        return spawn_limited_worker(self, target=target, args=args, name=name, target_id=target_id)

    def _send_or_log(self, host: str, port: int, payload: bytes, *, message_type: str, target_id: str) -> bool:
        return send_or_log(host, port, payload, message_type=message_type, target_id=target_id)


def main() -> int:

    # C15: the ONE bounded unattended preflight, before daemon bootstrap reaches any
    # OS credential API (signer identity, credential store, browser automation).
    from core.unattended_preflight import preflight

    preflight("core.agent_runtime.daemon")

    parser = argparse.ArgumentParser(prog="vool-daemon")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--bind-port", type=int, default=49152)
    parser.add_argument("--advertise-host", default="127.0.0.1")
    parser.add_argument("--capacity", default="auto")
    parser.add_argument("--health-host", default="127.0.0.1")
    parser.add_argument("--health-port", type=int, default=0)
    parser.add_argument("--health-token", default="")
    args = parser.parse_args()

    from core.runtime_bootstrap import bootstrap_runtime_mode

    bootstrap_runtime_mode(mode="daemon", force_policy_reload=True)

    # A8 pass-002: interrupted erasure traversals resume at the existing
    # startup lifecycle — an ERASE that crashed mid-sweep converges before
    # the daemon serves anything, never fabricating completion.
    try:
        from core.finalization import resume_incomplete_erasure_sweeps

        resumed = resume_incomplete_erasure_sweeps()
        if resumed:
            audit_logger.log(
                "a8_erasure_sweep_resume",
                target_id="local",
                target_type="daemon",
                details={"resumed": len(resumed)},
            )
    except Exception:
        # Honest failure: startup proceeds but the sweep stays incomplete and
        # observable via erasure_sweep_status — never a fabricated completion.
        audit_logger.log(
            "a8_erasure_sweep_resume_failed",
            target_id="local",
            target_type="daemon",
            details={},
        )

    pool_hard_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
    requested_capacity: int | None = None
    if str(args.capacity).strip().lower() != "auto":
        requested_capacity = max(1, int(args.capacity))
    daemon_capacity, recommended_capacity = resolve_local_worker_capacity(
        requested=requested_capacity,
        hard_cap=pool_hard_cap,
    )
    if daemon_capacity > recommended_capacity:
        print(
            "WARNING: daemon capacity override is above recommended "
            f"({daemon_capacity} > {recommended_capacity})."
        )

    daemon = VoolDaemon(
        DaemonConfig(
            bind_host=str(args.bind_host),
            bind_port=int(args.bind_port),
            advertise_host=str(args.advertise_host),
            capacity=max(1, int(daemon_capacity)),
            local_worker_threads=max(2, int(daemon_capacity) * 2),
            health_bind_host=str(args.health_host),
            health_bind_port=max(0, int(args.health_port)),
            health_auth_token=str(args.health_token or "").strip() or None,
        )
    )
    daemon.start()
    stop_event = threading.Event()

    def _request_stop(_signum, _frame) -> None:
        stop_event.set()

    # Register stop signals defensively: a given signal may not exist on every
    # platform (Windows lacks several POSIX signals) and signal.signal() raises
    # if called off the main thread. Either way the daemon should still start and
    # be stoppable via stop_event; missing a handler is not fatal.
    for _sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        _sig = getattr(signal, _sig_name, None)
        if _sig is None:
            continue
        try:
            signal.signal(_sig, _request_stop)
        except (ValueError, OSError, RuntimeError):
            continue
    try:
        while not stop_event.wait(1.0):
            continue
    finally:
        daemon.stop()
    return 0


__all__ = ["DaemonConfig", "NodeRuntime", "VoolDaemon", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
