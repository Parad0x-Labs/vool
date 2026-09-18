from __future__ import annotations
import platform
import threading
import time

from apps.meet_and_greet_server import MeetAndGreetServerConfig, build_server
from core import audit_logger, policy_engine
from core.logging_config import setup_logging
from core.meet_and_greet_models import MeetNodeRegisterRequest
from core.meet_and_greet_node_config import MeetAndGreetNodeConfig, MeetPeerSeed
from core.meet_and_greet_replication import MeetAndGreetReplicator, ReplicationConfig
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from core.runtime_bootstrap import bootstrap_storage_environment
from core.runtime_guard import enforce_meet_public_deployment

__all__ = [
    "MeetAndGreetNode",
    "MeetAndGreetNodeConfig",  # compatibility facade: moved to core (M1); remove once
    "MeetPeerSeed",            # callers import from core.meet_and_greet_node_config
]

from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)


class MeetAndGreetNode:
    def __init__(self, config: MeetAndGreetNodeConfig) -> None:
        self.config = config
        self.service = MeetAndGreetService(config.service_config)
        self.replicator = MeetAndGreetReplicator(self.service, config=config.replication_config)
        self.server = None
        self._server_thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        bootstrap_storage_environment()
        enforce_meet_public_deployment(
            bind_host=self.config.bind_host,
            public_base_url=self.config.public_base_url,
            auth_token=self.config.auth_token,
            tls_certfile=self.config.tls_certfile,
            tls_keyfile=self.config.tls_keyfile,
            allow_insecure_public_http=self.config.allow_insecure_public_http,
        )
        self.server = build_server(
            MeetAndGreetServerConfig(
                host=self.config.bind_host,
                port=self.config.bind_port,
                auth_token=self.config.auth_token,
                tls_certfile=self.config.tls_certfile,
                tls_keyfile=self.config.tls_keyfile,
                tls_ca_file=self.config.tls_ca_file,
                tls_require_client_cert=self.config.tls_require_client_cert,
            ),
            service=self.service,
        )
        self._register_self()
        self._register_seeds()
        self._stop.clear()
        self._server_thread = threading.Thread(target=self.server.serve_forever, name="meet-and-greet-server", daemon=True)
        self._server_thread.start()
        self._sync_thread = threading.Thread(target=self._sync_loop, name="meet-and-greet-sync", daemon=True)
        self._sync_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)

    def _register_self(self) -> None:
        self.service.register_meet_node(
            MeetNodeRegisterRequest(
                node_id=self.config.node_id,
                base_url=self.config.public_base_url,
                region=self.config.region,
                role=self.config.role,
                platform_hint=platform.system().lower(),
                priority=self.config.priority,
                status="active",
                metadata={"bind_host": self.config.bind_host, "bind_port": self.config.bind_port},
            )
        )

    def _register_seeds(self) -> None:
        for seed in self.config.seed_peers:
            if seed.node_id == self.config.node_id:
                continue
            self.service.register_meet_node(
                MeetNodeRegisterRequest(
                    node_id=seed.node_id,
                    base_url=seed.base_url,
                    region=seed.region,
                    role=seed.role,
                    platform_hint=seed.platform_hint,
                    priority=seed.priority,
                    status="active",
                    metadata={},
                )
            )

    def _sync_loop(self) -> None:
        while not self._stop.wait(self.config.sync_interval_seconds):
            try:
                self.replicator.sync_registered_nodes(exclude_node_id=self.config.node_id)
            except Exception as exc:
                audit_logger.log(
                    "meet_node_sync_error",
                    target_id=self.config.node_id,
                    target_type="meet_node",
                    details={"error": str(exc)},
                )
                time.sleep(1.0)
